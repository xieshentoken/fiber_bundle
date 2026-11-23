"""Request-owned evidence; observed Vaults admit only published index versions."""

import json
import sqlite3
from dataclasses import replace
from typing import Any, Self

from . import auth, note_index, note_inventory, note_search_cache
from .config import Config, IntegrityError
from .search_matching import best_passage, terms


class SearchSnapshot:
    moc_only = False

    def __init__(self, config: Config, db: sqlite3.Connection, client_id: str) -> None:
        self.config = config
        self.db = db
        self.client_id = client_id
        self.fingerprints: dict[str, str] = {}
        self.inventories: dict[str, note_inventory.Inventory] = {}
        self.managed: set[str] = set()
        self.reads: dict[tuple[str, str], tuple[Any, str]] = {}
        self.incomplete: set[str] = set()
        self.indexes: dict[str, Any] = {}
        self.experts_used: dict[str, dict[str, Any]] = {}

    def __enter__(self) -> Self:
        return self

    def mark_unavailable(
        self,
        vault_id: str,
        *,
        identity: str | None = None,
        path: str = "",
        reason: str = "moc_query_member_unavailable",
    ) -> None:
        self.incomplete.add(vault_id)

    def __exit__(self, kind: Any, value: Any, trace: Any) -> None:
        try:
            if kind is None:
                from .search import scope

                self.verify_experts()
                for vault in scope(self.db, self.client_id, list(self.fingerprints)):
                    if vault["id"] in self.managed:
                        from .vault_scope import for_vault
                        from .vault_sync import scanner
                        from .vaults import checked_vault

                        root = checked_vault(self.config, vault)
                        allowed = for_vault(root, vault)
                        from . import indexed_sources

                        current = indexed_sources.read(self.config, self.db, self.client_id, vault)
                        for (identity, path), (_, observed) in self.reads.items():
                            if identity == vault["id"] and (
                                not allowed.allows(path)
                                or path not in current.notes
                                or scanner.signature((root / path).lstat()) != observed
                            ):
                                raise IntegrityError("search evidence changed before delivery")
                        continue
                    if (
                        note_search_cache.fingerprint(self.config, self.db, vault)
                        != self.fingerprints[vault["id"]]
                    ):
                        raise IntegrityError("search evidence changed before delivery")
        finally:
            # No body, file observation or authorization survives this local phase.
            self.inventories.clear()
            self.fingerprints.clear()
            self.reads.clear()
            self.managed.clear()
            self.indexes.clear()
            self.experts_used.clear()

    def observed(self, vault: sqlite3.Row) -> str:
        auth.grant(self.db, self.client_id, vault["id"], "query")
        if vault["id"] not in self.fingerprints:
            from . import indexed_sources

            if indexed_sources.managed(self.config, vault["id"]):
                self.managed.add(vault["id"])
                current = indexed_sources.read(self.config, self.db, self.client_id, vault)
                self.indexes[vault["id"]] = current
                self.fingerprints[vault["id"]] = current.fingerprint
            else:
                self.fingerprints[vault["id"]] = note_search_cache.fingerprint(
                    self.config, self.db, vault
                )
        return self.fingerprints[vault["id"]]

    def inventory(self, vault: sqlite3.Row) -> note_inventory.Inventory:
        self.observed(vault)
        if vault["id"] in self.managed:
            if vault["id"] not in self.inventories:
                from . import note_search_inventory

                self.inventories[vault["id"]] = note_search_inventory.read(
                    self.config, self.db, vault, selected=set()
                )
            return self.inventories[vault["id"]]
        if vault["id"] not in self.inventories:
            # Match the established index library cap and unchanged per-note limits.
            self.inventories[vault["id"]] = note_inventory.read(
                self.config,
                self.db,
                vault,
                limits=replace(
                    note_index.Limits(), total_bytes=note_search_cache.MAX_LIBRARY_BYTES
                ),
            )
        return self.inventories[vault["id"]]

    def candidates(self, vault: sqlite3.Row, query: str) -> Any:
        observed = self.observed(vault)
        if vault["id"] in self.managed:
            current = self.indexes[vault["id"]]
            if current.fingerprint:
                cached = note_search_cache.candidates(
                    self.config,
                    self.db,
                    vault,
                    query,
                    observed_fingerprint=current.fingerprint,
                    partial=True,
                    allowed_paths=set(current.notes),
                    allowed_notes=current.notes,
                )
                if cached is not None:
                    return (*cached, True)
            return self.inventory(vault), [], True
        if vault["id"] not in self.inventories:
            try:
                cached = note_search_cache.candidates(
                    self.config, self.db, vault, query, observed_fingerprint=observed
                )
            except note_search_cache.IndexUnavailable as error:
                if vault["id"] in self.managed or str(error) != "note_index_update_required":
                    raise
                cached = None
            if cached is not None:
                return (*cached, True)
        inventory = self.inventory(vault)
        patterns = terms(query)
        return (
            inventory,
            [
                (best, note)
                for note in inventory.notes
                if (best := best_passage(note.title, note.text, patterns)) is not None
            ],
            False,
        )

    def titles(self, vault: sqlite3.Row) -> list[str]:
        observed = self.observed(vault)
        if vault["id"] in self.managed:
            return [n.title for n in self.indexes[vault["id"]].notes.values()]
        if vault["id"] not in self.inventories:
            with note_search_cache.connection(self.config, vault["id"]) as cache:
                generation = cache.execute("SELECT * FROM generation").fetchone() if cache else None
                if (
                    generation
                    and generation["state"] == "ready"
                    and generation["fingerprint"] == observed
                ):
                    assert cache is not None
                    return [
                        json.loads(row[0])["title"]
                        for row in cache.execute("SELECT metadata FROM notes ORDER BY path")
                    ]
        return [note.title for note in self.inventory(vault).notes]

    def read_note(self, identity: str, root: Any, relative: str) -> note_index.Note:
        from .vault_scope import for_vault
        from .vault_sync.scanner import signature

        vault = auth.grant(self.db, self.client_id, identity, "query")
        if not for_vault(root, vault).allows(relative):
            raise IntegrityError("search evidence outside current scope")
        before = signature((root / relative).lstat())
        note = note_index.read_note(root, relative, note_index.Limits().note_bytes)
        if signature((root / relative).lstat()) != before:
            raise IntegrityError("search evidence changed before delivery")
        self.reads[identity, relative] = (root, before)
        return note

    def coverage(self, identity: str) -> dict[str, Any] | None:
        from .vault_sync.index_state import coverage

        value = (
            self.indexes[identity].coverage
            if identity in self.indexes
            else coverage(self.config, identity)
        )
        if value is not None and identity in self.incomplete:
            value = {**value, "complete": False}
        return value

    def managed_entries(self, vault: sqlite3.Row) -> dict[str, Any]:
        from . import publication_locations

        return publication_locations.inventory(self.db, vault["id"])[0]

    def expert_ids(self, vault: sqlite3.Row) -> set[str] | None:
        self.observed(vault)
        return set(self.indexes[vault["id"]].experts) if vault["id"] in self.indexes else None

    def expert_version(self, vault: sqlite3.Row, identity: str) -> str | None:
        current = self.indexes.get(vault["id"])
        return current.experts[identity][0] if current is not None else None

    def use_expert(self, source: dict[str, Any]) -> None:
        from . import indexed_sources

        if indexed_sources.managed(self.config, source["vault_id"]):
            self.experts_used[source["expert_module_id"]] = dict(source)

    def verify_experts(self) -> None:
        from .indexed_expert_search import verify

        for source in self.experts_used.values():
            current = verify(self.config, self.db, self.client_id, source)
            if (current["note_path"], current["sha256"], current["snippet"]) != (
                source["note_path"],
                source["sha256"],
                source["snippet"],
            ):
                raise IntegrityError("search evidence changed before delivery")

    def scope_descriptor(self) -> dict[str, Any] | None:
        return None


def combine(
    experts: list[dict[str, Any]], notes: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    """Keep both relevant source families without treating a Draft as independent evidence."""
    result = []
    for index in range(max(len(experts), len(notes))):
        for group in (experts, notes):
            if index < len(group):
                result.append(group[index])
    return result[:limit]
