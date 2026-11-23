"""Phase-local direct reads and delivery checks for mounted MOC query evidence."""

import sqlite3
import stat
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from . import auth, moc_query_members, note_index, note_inventory, publication_locations
from .config import Config, IntegrityError, StorageError, contained
from .inputs import canonical
from .search_matching import best_passage, terms
from .search_snapshot import SearchSnapshot
from .vault_sync.scanner import signature


class MountedQuerySnapshot(SearchSnapshot):
    moc_only = True

    def __init__(
        self,
        config: Config,
        db: sqlite3.Connection,
        client_id: str,
        *,
        frozen_scope: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(config, db, client_id)
        self.scopes: dict[str, moc_query_members.MemberScope] = {}
        self.notes: dict[tuple[str, str], note_index.Note] = {}
        self.failures: dict[tuple[str, str], Exception] = {}
        self.used: dict[tuple[str, str], str] = {}
        self.checked: dict[str, tuple[Any, moc_query_members.MemberScope]] = {}
        self.frozen: dict[str, set[str]] | None = None
        self.hints: dict[str, dict[str, str]] = {}
        self.body_bytes = 0
        self.indexed: dict[str, Any] = {}
        if frozen_scope is not None:
            try:
                if frozen_scope["schema"] != 1 or not isinstance(frozen_scope["vaults"], dict):
                    raise ValueError()
                self.frozen = {}
                for vault, value in frozen_scope["vaults"].items():
                    members = value["members"]
                    if (
                        not isinstance(vault, str)
                        or not isinstance(members, list)
                        or len(members) > 10000
                        or any(not isinstance(i, str) for i in members)
                    ):
                        raise ValueError()
                    self.frozen[vault] = set(members)
                    paths = value.get("paths", {})
                    if not isinstance(paths, dict) or any(
                        i not in members or not isinstance(path, str) for i, path in paths.items()
                    ):
                        raise ValueError()
                    self.hints[vault] = paths
            except (ValueError, TypeError, KeyError):
                raise IntegrityError("moc_query_scope_invalid") from None

    def _load(self, vault: sqlite3.Row) -> moc_query_members.MemberScope:
        vid = vault["id"]
        auth.grant(self.db, self.client_id, vid, "query")
        if vid not in self.scopes:
            from .vault_scope import for_vault
            from .vaults import checked_vault

            root = checked_vault(self.config, vault)
            allowed = for_vault(root, vault)
            from . import indexed_sources

            if indexed_sources.managed(self.config, vid):
                self.indexed[vid] = indexed_sources.read(
                    self.config, self.db, self.client_id, vault
                )
            self.scopes[vid] = moc_query_members.MemberScope(root, allowed)
            try:
                self.scopes[vid] = moc_query_members.resolve(
                    self.config,
                    self.db,
                    vault,
                    lambda m: self._read(vid, m),
                    frozen=self.frozen.get(vid, set()) if self.frozen is not None else None,
                    hints=self.hints.get(vid),
                )
                if vid in self.indexed:
                    indexed = self.indexed[vid]
                    for identity, member in list(self.scopes[vid].members.items()):
                        included = (
                            member.path in indexed.notes
                            if member.kind == "note"
                            else identity.removeprefix("expert:") in indexed.experts
                        )
                        if not included:
                            self.scopes[vid].omit(
                                identity, "moc_query_source_not_indexed", member.path
                            )
                            self.scopes[vid].members.pop(identity, None)
                self.checked[vid] = (
                    self._stamp(vid, self.scopes[vid], observed_maps=True),
                    self.scopes[vid],
                )
            except BaseException:
                self.scopes.pop(vid, None)
                raise
        return self.scopes[vid]

    def _read(self, vid: str, member: moc_query_members.Member) -> note_index.Note:
        key = (vid, member.path)
        auth.grant(self.db, self.client_id, vid, "query")
        scope = self.scopes[vid]
        if not moc_query_members.readable(scope.allowed, member.path):
            raise IntegrityError("moc_query_member_scope_denied")
        cached = self.indexed[vid].notes.get(member.path) if vid in self.indexed else None
        if vid in self.indexed and cached is None:
            raise IntegrityError("moc_query_source_not_indexed")
        if key in self.failures:
            raise self.failures[key]
        if key not in self.notes:
            try:
                if self.body_bytes >= 64 * 1024 * 1024:
                    raise IntegrityError("moc_query_body_limit")
                path = contained(scope.root, member.path)
                before = signature(path.lstat())
                note = note_index.read_note(scope.root, member.path, note_index.Limits().note_bytes)
                if signature(path.lstat()) != before:
                    raise IntegrityError("search evidence changed before delivery")
                self.body_bytes += len(note.text.encode())
                if self.body_bytes > 64 * 1024 * 1024:
                    raise IntegrityError("moc_query_body_limit")
                self.notes[key] = note
                self.reads[key] = (scope.root, before)
            except (StorageError, OSError) as error:
                self.failures[key] = error
                raise
        note = self.notes[key]
        if cached is not None and (note.sha256, note.file_key) != (cached.sha256, cached.file_key):
            raise IntegrityError("moc_query_source_not_indexed")
        if member.registration and note.file_key != member.registration.file_key:
            raise IntegrityError("moc_query_member_identity_changed")
        if member.managed:
            note = publication_locations.annotate(note, member.managed)
        if member.identity and not member.registration and not member.managed:
            expected = "note:" + str(
                uuid5(
                    NAMESPACE_URL,
                    "fiber-organization-note:"
                    + vid
                    + ":"
                    + canonical(note.file_key or [member.path]),
                )
            )
            if member.identity != expected:
                raise IntegrityError("moc_query_member_identity_changed")
        if member.identity:
            note = replace(
                note,
                identity=member.identity,
                publication_id=member.registration.publication_id
                if member.registration
                else note.publication_id,
                publication_path=member.path.rsplit("/", 1)[0]
                if member.registration and member.registration.publication_id
                else note.publication_path,
            )
        return note

    def observed(self, vault: sqlite3.Row) -> str:
        self._load(vault)
        return "moc_members"

    def inventory(self, vault: sqlite3.Row) -> note_inventory.Inventory:
        vid = vault["id"]
        scope = self._load(vault)
        if vid not in self.inventories:
            notes, publications, bindings, locations = [], {}, {}, {}
            total = 0
            for member in scope.members.values():
                if member.kind != "note":
                    continue
                try:
                    if total >= 64 * 1024 * 1024:
                        raise IntegrityError("moc_query_body_limit")
                    note = self._read(vid, member)
                    total += len(note.text.encode())
                    if total > 64 * 1024 * 1024:
                        raise IntegrityError("moc_query_body_limit")
                    if note.publication_id:
                        row = self.db.execute(
                            "SELECT p.* FROM article_publications p "
                            "LEFT JOIN publication_locations l "
                            "ON l.kind='article' AND l.publication_id=p.id "
                            "WHERE p.id=? AND p.state='published' "
                            "AND COALESCE(l.vault_id,p.vault_id)=?",
                            (note.publication_id, vid),
                        ).fetchone()
                        if row is None:
                            raise IntegrityError("moc_query_publication_unavailable")
                        publications[row["id"]] = row
                        bindings[row["id"]] = publication_locations.binding(self.db, row)
                        locations[row["id"]] = member.path.rsplit("/", 1)[0]
                    notes.append(note)
                except (StorageError, OSError, auth.Denied):
                    scope.omit(member.identity, "moc_query_member_unavailable", member.path)
                    self.incomplete.add(vid)
            self.inventories[vid] = note_inventory.Inventory(
                scope.root,
                notes,
                publications,
                bindings,
                locations,
                coverage=self.coverage(vid),
                snapshot_token="moc_members",
            )
        return self.inventories[vid]

    def candidates(self, vault: sqlite3.Row, query: str) -> Any:
        patterns = terms(query)
        inventory = self.inventory(vault)
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
        return [n.title for n in self.inventory(vault).notes]

    def expert_ids(self, vault: sqlite3.Row) -> set[str]:
        return {
            i.removeprefix("expert:")
            for i, m in self._load(vault).members.items()
            if m.kind == "expert"
        }

    def has_members(self, vault: sqlite3.Row) -> bool:
        scope = self._load(vault)
        for member in scope.members.values():
            try:
                info = contained(scope.root, member.path).lstat()
                if member.kind == "expert":
                    if stat.S_ISDIR(info.st_mode):
                        return True
                elif stat.S_ISREG(info.st_mode) and (
                    member.registration is None
                    or (info.st_dev, info.st_ino, float(getattr(info, "st_birthtime", 0)).hex())
                    == member.registration.file_key
                ):
                    return True
            except (StorageError, OSError):
                pass
            scope.omit(member.identity, "moc_query_member_unavailable", member.path)
        return False

    def managed_entries(self, vault: sqlite3.Row) -> dict[str, dict[str, Any]]:
        return {
            m.path: m.managed for m in self._load(vault).members.values() if m.managed is not None
        }

    def scope_descriptor(self, vault_id: str | None = None) -> dict[str, Any]:
        return {
            "schema": 1,
            "vaults": {
                vid: {
                    "members": sorted(scope.members),
                    "paths": {
                        i: m.path
                        for i, m in scope.members.items()
                        if m.kind == "note" and m.registration is None and m.managed is None
                    },
                }
                for vid, scope in self.scopes.items()
                if vault_id is None or vault_id == vid
            },
        }

    def read_note(self, identity: str, root: Any, relative: str) -> note_index.Note:
        vault = auth.grant(self.db, self.client_id, identity, "query")
        scope = self._load(vault)
        if Path(root) != scope.root:
            raise IntegrityError("moc_query_vault_changed")
        member = next((m for m in scope.members.values() if m.path == relative), None)
        if member is None or member.kind != "note":
            raise IntegrityError("moc_query_source_outside_members")
        note = self._read(identity, member)
        self.used[identity, relative] = member.identity
        self.assert_source(identity, relative, member.identity)
        return note

    def assert_source(self, vault_id: str, path: str, identity: str | None = None) -> None:
        vault = auth.grant(self.db, self.client_id, vault_id, "query")
        previous = self._load(vault)
        stamp = self._stamp(vault_id, previous)
        checked = self.checked.get(vault_id)
        if checked is not None and checked[0] == stamp:
            current = checked[1]
        else:
            current = moc_query_members.resolve(
                self.config,
                self.db,
                vault,
                lambda m: self._read(vault_id, m),
                frozen=set(previous.members),
                hints={
                    i: m.path
                    for i, m in previous.members.items()
                    if m.kind == "note" and m.registration is None and m.managed is None
                },
            )
            observed = self._stamp(vault_id, current, observed_maps=True)
            if self._stamp(vault_id, current) != observed:
                raise IntegrityError("search evidence changed before delivery")
            self.checked[vault_id] = (observed, current)
        member = next(
            (
                m
                for m in current.members.values()
                if m.path == path or (m.kind == "expert" and path.startswith(m.path + "/"))
            ),
            None,
        )
        if member is None or (identity is not None and member.identity != identity):
            raise IntegrityError("moc_query_member_removed_or_changed")
        if current.root != previous.root or not moc_query_members.readable(current.allowed, path):
            raise IntegrityError("moc_query_source_scope_changed")
        if member.kind == "expert":
            from . import indexed_sources, published_expert

            if vault_id in self.indexed:
                module = published_expert.text(
                    self.config, self.db, self.client_id, member.identity.removeprefix("expert:")
                )
                if not indexed_sources.expert_allowed(self.config, self.db, self.client_id, module):
                    raise IntegrityError("moc_query_source_not_indexed")
            self.used[vault_id, path] = member.identity
            return
        note = self._read(vault_id, member)
        observed = self.reads[vault_id, path][1]
        if signature(contained(current.root, path).lstat()) != observed:
            raise IntegrityError("search evidence changed before delivery")
        if identity is not None and note.identity != identity:
            raise IntegrityError("moc_query_member_identity_changed")
        self.used[vault_id, path] = member.identity

    def _stamp(
        self, vid: str, scope: moc_query_members.MemberScope, *, observed_maps: bool = False
    ) -> Any:
        """Cheap local invalidation; no note index, recursive scan or body cache."""
        from .vault_scope import for_vault
        from .vaults import checked_vault

        row = auth.grant(self.db, self.client_id, vid, "query")
        root = checked_vault(self.config, row)
        allowed = for_vault(root, row)
        files: list[tuple[str, str | None]] = []
        for name in (
            "moc-associations.sqlite3",
            "note-identities.sqlite3",
            "organization.sqlite3",
            "note-migration-executions.sqlite3",
        ):
            for suffix in ("", "-wal", "-journal"):
                try:
                    files.append(
                        (
                            name + suffix,
                            signature(contained(self.config.state_root, name + suffix).lstat()),
                        )
                    )
                except FileNotFoundError:
                    files.append((name + suffix, None))
        for path in sorted(scope.moc_paths):
            if not moc_query_members.readable(allowed, path):
                files.append((path, None))
                continue
            if observed_maps and path in scope.moc_signatures:
                files.append((path, scope.moc_signatures[path]))
                continue
            try:
                files.append((path, signature(contained(root, path).lstat())))
            except (StorageError, OSError):
                files.append((path, None))
        return (
            str(root),
            allowed.wire(),
            self.db.total_changes,
            self.db.execute("PRAGMA data_version").fetchone()[0],
            files,
        )

    def coverage(self, identity: str) -> dict[str, Any] | None:
        if identity not in self.scopes:
            return None
        scope = self.scopes[identity]
        unavailable = scope.unavailable
        candidates = scope.candidate_unavailable
        return {
            "scope": "moc_members",
            "complete": not unavailable and not candidates and identity not in self.incomplete,
            "member_count": len(scope.members),
            "unavailable_count": len(unavailable),
            "unavailable": list(unavailable),
            "candidate_unavailable_count": len(candidates),
            "candidate_unavailable": list(candidates),
        }

    def mark_unavailable(
        self,
        vault_id: str,
        *,
        identity: str | None = None,
        path: str = "",
        reason: str = "moc_query_member_unavailable",
    ) -> None:
        """Report one failed member at another query adapter boundary."""
        scope = self.scopes.get(vault_id)
        if scope is not None:
            if identity is None:
                identity = next(
                    (i for i, member in scope.members.items() if member.path == path), "member"
                )
            scope.omit(identity, reason, path)
        self.incomplete.add(vault_id)

    def __exit__(self, kind: Any, value: Any, trace: Any) -> None:
        try:
            if kind is None:
                self.verify_experts()
                for (vault, path), identity in list(self.used.items()):
                    self.assert_source(vault, path, identity)
        finally:
            self.scopes.clear()
            self.notes.clear()
            self.failures.clear()
            self.used.clear()
            self.checked.clear()
            self.inventories.clear()
            self.reads.clear()
            self.body_bytes = 0
            self.indexed.clear()
            self.experts_used.clear()

    def expert_version(self, vault: sqlite3.Row, identity: str) -> str | None:
        current = self.indexed.get(vault["id"])
        return current.experts[identity][0] if current is not None else None
