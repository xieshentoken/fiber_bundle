"""Resolve mounted query sources from bounded ledgers, never a Vault-wide scan.

The association ledger is authoritative for managed membership. Human links are
checked only at already observed manual candidates and explicit MOC backlinks.
Publication and registration metadata locate sources; neither is a body index.
"""

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from . import (
    auth,
    moc_file_journal,
    moc_markdown,
    moc_store,
    note_identities,
    note_identity_store,
    note_index,
    note_migration_store,
    publication_locations,
    vault_scope,
    vaults,
)
from .config import Config, IntegrityError, StorageError, contained
from .inputs import canonical
from .organization import note_moc_sync, store
from .organization.manual_relations import ManualRelations
from .vault_sync.scanner import signature

MAX_METADATA = 10000


@dataclass(frozen=True)
class Member:
    identity: str
    path: str
    kind: str = "note"
    registration: note_identity_store.Registration | None = None
    managed: dict[str, Any] | None = None


@dataclass
class MemberScope:
    root: Path
    allowed: vault_scope.Scope
    members: dict[str, Member] = field(default_factory=dict)
    unavailable: list[dict[str, str]] = field(default_factory=list)
    candidate_unavailable: list[dict[str, str]] = field(default_factory=list)
    moc_paths: set[str] = field(default_factory=set)
    moc_signatures: dict[str, str] = field(default_factory=dict)

    def omit(self, identity: str, reason: str, path: str = "", *, confirmed: bool = True) -> None:
        value = {"identity": identity, "reason": reason}
        if path:
            value["path"] = path
        target = self.unavailable if confirmed else self.candidate_unavailable
        if value not in target:
            target.append(value)


def readable(allowed: vault_scope.Scope, path: Any) -> bool:
    """Reject protected and malformed names before even probing their metadata."""
    return (
        isinstance(path, str)
        and len(path) <= 2048
        and not path.startswith("/")
        and "\\" not in path
        and "\0" not in path
        and all(p not in {"", ".", ".."} and not p.startswith(".") for p in path.split("/"))
        and allowed.allows(path)
    )


def _rows(config: Config, table: str, vault: str) -> list[dict[str, Any]]:
    """One malformed unrelated organizer item must not poison all query members."""
    assert table in store.TABLES
    with store.connection(config) as db:
        if (
            db is None
            or not db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone()
        ):
            return []
        rows = db.execute(
            f"SELECT value FROM {table} WHERE vault=? ORDER BY id LIMIT ?",
            (vault, MAX_METADATA + 1),
        ).fetchall()
    if len(rows) > MAX_METADATA:
        raise IntegrityError("moc_query_metadata_limit")
    result = []
    for row in rows:
        try:
            value = json.loads(row[0])
            if isinstance(value, dict):
                result.append(value)
        except (ValueError, TypeError, RecursionError):
            continue
    return result


def _registrations(config: Config, vault: str) -> dict[str, note_identity_store.Registration]:
    with note_identity_store.connection(config) as db:
        rows = (
            db.execute(
                "SELECT * FROM registrations WHERE vault_id=? ORDER BY identity LIMIT ?",
                (vault, MAX_METADATA + 1),
            ).fetchall()
            if db
            else []
        )
    if len(rows) > MAX_METADATA:
        raise IntegrityError("moc_query_metadata_limit")
    result = {}
    for row in rows:
        try:
            value = note_identity_store.Registration(
                row["identity"],
                row["publication_id"],
                row["relative_path"],
                row["sha256"],
                (row["file_dev"], row["file_ino"], row["file_birth"]),
                (row["vault_dev"], row["vault_ino"]),
                row["revision"],
            )
            note_identities.validate(value)
            birth = float.fromhex(value.file_key[2])
            if birth.hex() != value.file_key[2]:
                continue
            result[value.identity] = value
        except (StorageError, ValueError, TypeError, KeyError):
            continue
    return result


def _locations(db: sqlite3.Connection, vault: str) -> dict[str, list[Member]]:
    # Only compact metadata crosses the SQL boundary. Recovery blobs and output
    # bytes remain in SQLite, and malformed unrelated JSON is excluded in SQL.
    rows = db.execute(
        "SELECT kind,publication_id,vault_id,entry_note_path,package_root,state,"
        "json_extract(value,'$.mapping','$.file_keys','$.identity_overrides',"
        "'$.body_files','$.supplement_roles') AS metadata "
        "FROM publication_locations WHERE vault_id=? AND json_valid(value) LIMIT ?",
        (vault, MAX_METADATA + 1),
    ).fetchall()
    if len(rows) > MAX_METADATA:
        raise IntegrityError("moc_query_metadata_limit")
    result: dict[str, list[Member]] = {}
    for row in rows:
        try:
            mapping, keys, overrides, bodies, roles = json.loads(row["metadata"])
            if not isinstance(mapping, dict) or len(mapping) > MAX_METADATA:
                continue
            keys, overrides, roles = keys or {}, overrides or {}, roles or {}
            if not all(isinstance(v, dict) for v in (keys, overrides, roles)):
                continue
            for logical, path in mapping.items():
                if not isinstance(logical, str) or not logical.endswith(".md"):
                    continue
                if bodies is not None and logical not in bodies:
                    continue
                if (
                    not row["package_root"]
                    and logical != "note.md"
                    and roles.get(logical) != "analysis"
                ):
                    continue
                identity = overrides.get(logical) or publication_locations.note_id(
                    row["kind"], row["publication_id"], logical
                )
                moc_store.stable_identity(identity)
                managed = {
                    "kind": row["kind"],
                    "publication_id": row["publication_id"],
                    "logical": logical,
                    "identity": identity,
                    "package_root": row["package_root"],
                    "file_key": keys.get(path),
                    "state": row["state"],
                }
                result.setdefault(identity, []).append(Member(identity, path, managed=managed))
        except (StorageError, ValueError, TypeError, KeyError, RecursionError):
            continue
    return result


def _pending(config: Config, db: sqlite3.Connection, vault: str) -> tuple[set[str], set[str]]:
    identities: set[str] = set()
    paths: set[str] = set()
    rows = db.execute(
        "SELECT kind,json_extract(payload,'$.identity','$.path','$.moves',"
        "'$.source_root','$.target_root','$.publication_id') AS metadata,"
        "json_extract(payload,'$.source_path','$.target_path','$.mapping') AS locations,"
        "(SELECT json_group_array(json_extract("
        "CASE WHEN u.type='object' THEN u.value ELSE '{}' END,'$.path')) "
        "FROM json_each(payload,'$.updates') u) AS updates "
        "FROM vault_operations WHERE vault_id=? AND state IN ('applying','reconcile') "
        "AND json_valid(payload) LIMIT ?",
        (vault, MAX_METADATA + 1),
    ).fetchall()
    if len(rows) > MAX_METADATA:
        raise IntegrityError("moc_query_metadata_limit")
    for row in rows:
        try:
            identity, path, moves, source, target, publication = json.loads(row["metadata"])
            if isinstance(identity, str):
                identities.add(identity)
            if isinstance(publication, str):
                identities.add("publication:" + publication)
            paths.update(v for v in (path, source, target) if isinstance(v, str))
            if isinstance(moves, dict):
                paths.update(v for v in [*moves, *moves.values()] if isinstance(v, str))
            paths.update(v for v in json.loads(row["updates"]) if isinstance(v, str))
            source_path, target_path, mapping = json.loads(row["locations"])
            paths.update(v for v in (source_path, target_path) if isinstance(v, str))
            if isinstance(mapping, dict):
                paths.update(v for v in mapping.values() if isinstance(v, str))
        except (ValueError, TypeError, KeyError, RecursionError):
            continue
    # Cross-Vault claims have a separate ledger; isolate the claimed member.
    with note_migration_store.connection(config) as side:
        rows = (
            side.execute(
                "SELECT json_extract(intent_json,'$.registration.identity',"
                "'$.plan.source_path','$.plan.target_path') FROM intents "
                "WHERE phase='claimed' AND (source=? OR target=?) "
                "AND json_valid(intent_json) LIMIT ?",
                (vault, vault, MAX_METADATA + 1),
            ).fetchall()
            if side
            else []
        )
    if len(rows) > MAX_METADATA:
        raise IntegrityError("moc_query_metadata_limit")
    for row in rows:
        identity, source, target = json.loads(row[0])
        if isinstance(identity, str):
            identities.add(identity)
        paths.update(v for v in (source, target) if isinstance(v, str))
    return identities, paths


def resolve(
    config: Config,
    db: sqlite3.Connection,
    vault: sqlite3.Row,
    read_candidate: Callable[[Member], note_index.Note],
    *,
    frozen: set[str] | None = None,
    hints: dict[str, str] | None = None,
) -> MemberScope:
    """Resolve finite current membership; the caller owns authorization and body cache."""
    vid = vault["id"]
    root = vaults.checked_vault(config, vault)
    result = MemberScope(root, vault_scope.for_vault(root, vault))
    # Match catalog's entity boundary without invoking save-policy write guards
    # or inspecting unrelated directories. Maps remain readable for relations.
    policy = db.execute(
        "SELECT json_extract(value,'$.settings.moc_dir') FROM vault_save_policies WHERE vault_id=?",
        (vid,),
    ).fetchone()
    moc_dir = vault_scope.prefix(policy[0] if policy and policy[0] is not None else "Map")
    if not moc_dir:
        raise IntegrityError("moc_query_map_directory_invalid")
    info = root.stat()
    root_key = (info.st_dev, info.st_ino)
    registrations = _registrations(config, vid)
    locations = _locations(db, vid)
    packages = {p.get("module_id"): p for p in _rows(config, "packages", vid)}
    # Native published Drafts have the same expert membership identity, but their
    # publication ledger is separate from externally registered packages.
    for published in db.execute(
        "SELECT p.task_id,COALESCE(l.package_root,p.relative_path) AS path "
        "FROM source_expert_publications p JOIN source_imports i ON i.id=p.task_id "
        "LEFT JOIN publication_locations l ON l.kind='expert' AND l.publication_id=p.task_id "
        "WHERE p.state='published' AND COALESCE(l.vault_id,i.vault_id)=? "
        "AND (l.publication_id IS NULL OR l.state='published')",
        (vid,),
    ):
        if readable(result.allowed, published["path"]):
            packages["expert:" + published["task_id"]] = {
                "path": published["path"],
                "vault_key": root_key,
            }
    excluded = {
        (d.get("entity"), d.get("moc"))
        for d in _rows(config, "decisions", vid)
        if d.get("state") in {"rejected", "retired"}
    }
    projections = {p.get("entity"): p for p in _rows(config, "note_moc", vid)}
    pending_ids, pending_paths = _pending(config, db, vid)
    ledger: dict[str, set[str]] = {}
    mocs = []
    map_bytes = 0
    with moc_store.connection(config) as side:
        ids = (
            [
                r[0]
                for r in side.execute(
                    "SELECT id FROM mocs WHERE vault_id=? ORDER BY id LIMIT 1001", (vid,)
                )
            ]
            if side
            else []
        )
    if len(ids) > moc_store.MAX_MOCS:
        raise IntegrityError("moc_query_metadata_limit")
    for mid in ids:
        try:
            record = moc_store.load(config, vid, mid)
            if record is None or (record.vault_dev, record.vault_ino) != root_key:
                result.omit(mid, "moc_query_map_identity_changed")
                continue
            for identity in record.members:
                if (identity, mid) not in excluded:
                    ledger.setdefault(identity, set()).add(mid)
            plans = moc_file_journal.plans(config, vid, mid)
            plan = moc_file_journal.latest(plans)
            if plan is None:
                continue  # Metadata-only associations are a supported existing format.
            path = plan.value["path"]
            if not readable(result.allowed, path):
                continue
            result.moc_paths.add(path)
            if map_bytes >= 32 * 1024 * 1024:
                raise IntegrityError("moc_query_map_body_limit")
            map_file = contained(root, path)
            observed = signature(map_file.lstat())
            note = note_index.read_note(root, path, note_index.Limits().note_bytes)
            if signature(map_file.lstat()) != observed:
                raise IntegrityError("moc_query_map_changed")
            result.moc_signatures[path] = observed
            map_bytes += len(note.text.encode())
            if map_bytes > 32 * 1024 * 1024:
                raise IntegrityError("moc_query_map_body_limit")
            moc_markdown.split(note.text.encode(), mid)
            mocs.append({"id": mid, "path": path, "text": note.text, "members": record.members})
        except (StorageError, OSError, UnicodeError, ValueError, TypeError, KeyError):
            result.omit(mid, "moc_query_map_unavailable")

    def locate(identity: str, fallback: str | None = None) -> Member | None:
        if identity.startswith("expert:"):
            package = packages.get(identity)
            if package is not None and tuple(package.get("vault_key", ())) == root_key:
                return Member(identity, package["path"], "expert")
            return None
        registered = registrations.get(identity)
        mapped = locations.get(identity, [])
        if len(mapped) > 1:
            return None
        managed = mapped[0] if mapped else None
        if managed and not publication_locations.committed(db, managed.managed or {}):
            return None
        if registered:
            if registered.vault_key != root_key:
                return None
            if managed and (
                managed.path != registered.relative_path
                or (managed.managed or {}).get("file_key") != list(registered.file_key)
            ):
                return None
            return Member(
                identity,
                registered.relative_path,
                registration=registered,
                managed=managed.managed if managed else None,
            )
        return managed or (Member(identity, fallback) if fallback else None)

    def is_map(path: Any) -> bool:
        return isinstance(path, str) and (
            vault_scope.inside(path, moc_dir) or path in result.moc_paths
        )

    def retain(
        identity: str, fallback: str | None = None, *, confirmed: bool = True
    ) -> Member | None:
        if frozen is not None and identity not in frozen:
            return None
        registered = registrations.get(identity)
        mapped = locations.get(identity, [])
        if (
            is_map(fallback)
            or (registered is not None and is_map(registered.relative_path))
            or (len(mapped) == 1 and is_map(mapped[0].path))
        ):
            return None
        member = locate(identity, fallback)
        if member is None:
            result.omit(
                identity,
                "moc_query_member_location_unavailable"
                if confirmed
                else "moc_query_candidate_location_unavailable",
                fallback or "",
                confirmed=confirmed,
            )
            return None
        if member.kind == "note" and is_map(member.path):
            return None  # GUI catalog does not expose Map pages as note entities.
        if not readable(result.allowed, member.path):
            result.omit(
                identity,
                "moc_query_member_scope_denied"
                if confirmed
                else "moc_query_candidate_scope_denied",
                confirmed=confirmed,
            )
            return None
        if (
            identity in pending_ids
            or any(vault_scope.inside(member.path, path) for path in pending_paths)
            or projections.get(identity, {}).get("pending")
        ):
            result.omit(
                identity,
                "moc_query_member_pending" if confirmed else "moc_query_candidate_pending",
                member.path,
                confirmed=confirmed,
            )
            return None
        return member

    for identity in sorted(ledger):
        if member := retain(identity):
            result.members[identity] = member
    manual = ManualRelations(mocs)
    # Saved finite locators survive a model boundary. They are only hints: the
    # current human relationship and physical identity are still checked below.
    candidates: dict[str, str] = dict(hints or {})
    for item in _rows(config, "entities", vid):
        if (
            item.get("manual")
            and isinstance(item.get("entity"), str)
            and isinstance(item.get("path"), str)
        ):
            candidates[item["entity"]] = item["path"]
    for item in _rows(config, "manual_resolutions", vid):
        if (
            item.get("state") == "resolved"
            and item.get("method") == "manual_moc"
            and isinstance(item.get("entity"), str)
            and isinstance(item.get("path"), str)
        ):
            candidates[item["entity"]] = item["path"]
    # Backlinks resolve against bounded location metadata, with ambiguity retained.
    paths: dict[str, list[Member]] = {}
    known = {
        r.identity: Member(r.identity, r.relative_path, registration=r)
        for r in registrations.values()
    }
    known.update(
        {identity: values[0] for identity, values in locations.items() if len(values) == 1}
    )
    for member in known.values():
        if is_map(member.path):
            continue
        for target in {member.path.removesuffix(".md"), PurePosixPath(member.path).stem}:
            paths.setdefault(target, []).append(member)
    for target in manual.backlinks:
        found = paths.get(target, [])
        if len(found) == 1:
            candidates[found[0].identity] = found[0].path
        elif (
            frozen is None
            and not found
            and "/" in target
            and not is_map(target + ".md")
            and readable(result.allowed, target + ".md")
        ):
            # An explicit complete backlink is itself a finite observation.
            try:
                path = target + ".md"
                note = read_candidate(Member("", path))
                identity = "note:" + str(
                    uuid5(
                        NAMESPACE_URL,
                        "fiber-organization-note:" + vid + ":" + canonical(note.file_key or [path]),
                    )
                )
                candidates[identity] = path
            except (StorageError, OSError, auth.Denied):
                result.omit(
                    "manual:" + target,
                    "moc_query_candidate_unavailable",
                    target + ".md",
                    confirmed=False,
                )
    for identity, path in sorted(candidates.items()):
        if identity in result.members or identity.startswith("expert:"):
            continue
        member = retain(identity, path, confirmed=identity in ledger)
        if member is None:
            continue
        try:
            note = read_candidate(member)
            entity = {
                "id": identity,
                "kind": "note",
                "path": member.path,
                "text": note.text,
                "sha256": note.sha256,
            }
            note_moc_sync.decorate(entity, projections.get(identity))
            if any((identity, mid) not in excluded for mid in manual.memberships(entity)):
                result.members[identity] = member
        except (StorageError, OSError, auth.Denied):
            result.omit(
                identity,
                "moc_query_member_unavailable"
                if identity in ledger
                else "moc_query_candidate_unavailable",
                member.path,
                confirmed=identity in ledger,
            )
    if frozen is not None:
        for identity in frozen - result.members.keys():
            if not any(
                item["identity"] == identity
                for item in [*result.unavailable, *result.candidate_unavailable]
            ):
                result.omit(identity, "moc_query_member_removed")
    return result
