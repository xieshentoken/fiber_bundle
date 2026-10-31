"""Request-owned current sources; a persistent index is only an optional accelerator."""

import json
import sqlite3
from dataclasses import replace
from typing import Any

from . import auth, note_index, note_inventory, note_search_cache
from .config import Config, IntegrityError
from .search_matching import best_passage, terms


class SearchSnapshot:
    def __init__(self, config: Config, db: sqlite3.Connection, client_id: str) -> None:
        self.config = config
        self.db = db
        self.client_id = client_id
        self.fingerprints: dict[str, str] = {}
        self.inventories: dict[str, note_inventory.Inventory] = {}
        self.managed: set[str] = set()
        self.reads: dict[tuple[str, str], tuple[Any, str]] = {}
        self.incomplete: set[str] = set()

    def __enter__(self) -> "SearchSnapshot":
        return self

    def __exit__(self, kind: Any, value: Any, trace: Any) -> None:
        try:
            if kind is None:
                from .search import scope

                for vault in scope(self.db, self.client_id, list(self.fingerprints)):
                    if vault["id"] in self.managed:
                        from .vault_scope import for_vault
                        from .vault_sync import index_state, scanner
                        from .vaults import checked_vault

                        root = checked_vault(self.config, vault)
                        allowed = for_vault(root, vault)
                        index_state.token(self.config, self.db, vault)
                        for (identity, path), (_, observed) in self.reads.items():
                            if identity == vault["id"] and (
                                not allowed.allows(path)
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

    def observed(self, vault: sqlite3.Row) -> str:
        auth.grant(self.db, self.client_id, vault["id"], "query")
        if vault["id"] not in self.fingerprints:
            from .vault_sync.index_state import token

            observed = token(self.config, self.db, vault)
            if observed is not None:
                self.managed.add(vault["id"])
            self.fingerprints[vault["id"]] = observed or note_search_cache.fingerprint(
                self.config, self.db, vault
            )
        return self.fingerprints[vault["id"]]

    def inventory(self, vault: sqlite3.Row) -> note_inventory.Inventory:
        self.observed(vault)
        if vault["id"] in self.managed:
            raise note_search_cache.IndexUnavailable("note_index_update_required")
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

        value = coverage(self.config, identity)
        if value is not None and identity in self.incomplete:
            value = {**value, "complete": False}
        return value


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
