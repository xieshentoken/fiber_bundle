"""Explicit location repair after an external editor or whole-package move; no note writes."""

import json
import sqlite3
import stat
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import (
    auth,
    database,
    note_identity_store,
    note_placement,
    publication_locations,
    vault_directories,
    vault_save_policy,
    vault_scope,
)
from .config import Config, IntegrityError, InvalidInput, contained
from .inputs import canonical
from .objects import digest
from .publication_file_io import read, tree


def repair_context(
    config: Config, db: sqlite3.Connection, client: str, vault: str
) -> tuple[Path, vault_scope.Scope, dict[str, Any]]:
    """Repair history even if external directory changes invalidated the save-rule binding.

    This does not enable publication or change its policy. Current root identity, scope,
    query/add grants and the frozen policy revision/binding still gate every repair.
    """
    for permission in ("query", "add"):
        auth.grant(db, client, vault, permission)
    root, scope, _ = vault_directories.vault_root(config, db, vault)
    return root, scope, vault_save_policy.get(config, db, vault)


def observe(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vault: str,
    kind: str,
    identity: str,
    revision: int,
    target: str,
) -> dict[str, Any]:
    root, scope, policy = repair_context(config, db, client, vault)
    location = publication_locations.get(db, kind, identity)
    if (
        not location
        or location["vault_id"] != vault
        or not publication_locations.committed(db, {**location, "state": "published"})
        or location["state"] not in {"published", "missing"}
    ):
        raise InvalidInput("layout_rebind_published_source_required")
    if location["revision"] != revision:
        raise IntegrityError("layout_rebind_revision_changed")
    if (
        vault_scope.prefix(target) != target
        or not target.endswith(".md")
        or any(p.startswith(".") for p in target.split("/"))
    ):
        raise InvalidInput("layout_rebind_invalid_entry")
    package = location["package_root"]
    target_root = target.removesuffix("/note.md") if package else None
    if package and (target_root == target or not target_root):
        raise InvalidInput("layout_rebind_whole_package_required")
    moved = target != location["entry_note_path"]
    if moved and contained(root, package or location["entry_note_path"]).exists():
        raise IntegrityError("layout_rebind_original_still_present")
    mapping = {
        logical: (
            target_root + path[len(package) :]
            if package and target_root
            else target
            if logical == "note.md"
            else path
        )
        for logical, path in location["plan"]["mapping"].items()
    }
    other_paths = {
        path
        for row in note_placement.locations(db, vault)
        if (row["kind"], row["publication_id"]) != (kind, identity)
        for path in row["plan"]["outputs"]
    }
    if other_paths.intersection(mapping.values()):
        raise IntegrityError("layout_rebind_identity_collision")
    if package and set(tree(root, str(target_root), scope)) != {
        path.removeprefix(str(target_root) + "/") for path in mapping.values()
    }:
        raise IntegrityError("layout_rebind_package_members_changed")
    files = {}
    modified = []
    for logical, path in mapping.items():
        if not scope.allows(path):
            raise InvalidInput("layout_rebind_scope_denied")
        raw, key = read(root, path)
        oldpath = location["plan"]["mapping"][logical]
        expected = location["plan"].get("write_hashes", location["plan"]["published_hashes"])[
            oldpath
        ]
        if digest(raw) != expected:
            if not logical.endswith(".md"):
                raise IntegrityError("layout_rebind_resource_changed")
            modified.append(path)
        files[path] = {"sha256": digest(raw), "file_key": key}
    # A second copy cannot steal the still-visible physical identity of the original.
    old_keys = {tuple(k[:2]) for k in location["plan"]["file_keys"].values()}
    for path, info in vault_scope.entries(root, scope):
        if stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) in old_keys:
            if path.relative_to(root).as_posix() not in files:
                raise IntegrityError("layout_rebind_original_still_present")
    binding = db.execute(
        "SELECT value FROM vault_save_policies WHERE vault_id=?", (vault,)
    ).fetchone()
    return {
        "client_id": client,
        "vault_id": vault,
        "kind": kind,
        "publication_id": identity,
        "location_revision": revision,
        "root_key": vault_directories.key(root),
        "policy_revision": policy["revision"],
        "policy_binding": binding[0] if binding else None,
        "source_path": location["entry_note_path"],
        "target_path": target,
        "package_root": target_root,
        "mapping": mapping,
        "files": files,
        "modified_notes": modified,
        "scope": scope.wire(),
    }


def result(operation: str, state: str, value: dict[str, Any]) -> dict[str, Any]:
    return {
        "operation_id": operation,
        "state": state,
        "ready": True,
        "source_path": value["source_path"],
        "target_path": value["target_path"],
        "file_mapping": value["mapping"],
        "modified_notes": value["modified_notes"],
        "entry_sha256": value["files"][value["target_path"]]["sha256"],
        "vault_write_performed": False,
        "unresolved_links": [],
        "association_state": "preserved",
        "link_review_required": True,
    }


def preview(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vault: str,
    kind: str,
    identity: str,
    revision: int,
    target: str,
) -> dict[str, Any]:
    note_placement.guard(db, vault)
    value = observe(config, db, client, vault, kind, identity, revision, target)
    value["expires_at"] = time.time() + 600
    value["registrations"] = [asdict(r) for r in note_identity_store.records(config, vault)]
    operation = str(uuid4())
    db.execute(
        "INSERT INTO vault_operations VALUES(?,?,?,'prepared',?,?,?)",
        (operation, vault, "publication_rebind", canonical(value), time.time(), time.time()),
    )
    return result(operation, "prepared", value)


def apply(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vault: str,
    operation: str,
    *,
    reconcile: bool = False,
    cancel: bool = False,
) -> dict[str, Any]:
    row = db.execute(
        "SELECT * FROM vault_operations WHERE id=? AND vault_id=? AND kind='publication_rebind'",
        (operation, vault),
    ).fetchone()
    if not row:
        raise auth.Denied(404)
    value = json.loads(row["payload"])
    if value["client_id"] != client:
        raise auth.Denied(404)
    for permission in ("query", "add"):
        auth.grant(db, client, vault, permission)
    if cancel:
        if row["state"] not in {"prepared", "cancelled"}:
            raise IntegrityError("placement_reconciliation_required")
        db.execute("UPDATE vault_operations SET state='cancelled' WHERE id=?", (operation,))
        return {"state": "cancelled"}
    if row["state"] == "completed":
        return result(operation, "completed", value)
    if row["state"] != "prepared" and not reconcile:
        raise IntegrityError("placement_reconciliation_required")
    if row["state"] == "cancelled" or (
        row["state"] == "prepared" and time.time() > value["expires_at"]
    ):
        raise IntegrityError("placement_preview_expired")
    current = observe(
        config,
        db,
        client,
        vault,
        value["kind"],
        value["publication_id"],
        value["location_revision"],
        value["target_path"],
    )
    if any(canonical(v) != canonical(value[k]) for k, v in current.items()):
        raise IntegrityError("layout_rebind_preview_changed")
    db.execute("UPDATE vault_operations SET state='applying' WHERE id=?", (operation,))
    try:
        location = publication_locations.get(db, value["kind"], value["publication_id"])
        assert location is not None
        plan = location["plan"]
        plan.pop("availability", None)
        moves = {plan["mapping"][n]: path for n, path in value["mapping"].items()}
        for before in value["registrations"]:
            path = moves.get(before["relative_path"])
            if path is None:
                continue
            registration = note_identity_store.Registration(
                before["identity"],
                before["publication_id"],
                before["relative_path"],
                before["sha256"],
                tuple(before["file_key"]),
                tuple(before["vault_key"]),
                before["revision"],
            )
            after = replace(
                registration,
                relative_path=path,
                sha256=value["files"][path]["sha256"],
                file_key=tuple(value["files"][path]["file_key"]),
                revision=registration.revision + 1,
            )
            existing = next(
                (
                    r
                    for r in note_identity_store.records(config, vault)
                    if r.identity == before["identity"]
                ),
                None,
            )
            if existing == after:
                continue
            if existing != registration:
                raise IntegrityError("layout_rebind_identity_changed")
            note_identity_store.save(
                config, vault, client, after, expected_revision=registration.revision
            )
        for field in ("outputs", "published_hashes", "claims", "file_keys", "write_hashes"):
            if field in plan:
                plan[field] = {moves.get(path, path): v for path, v in plan[field].items()}
        plan["mapping"] = value["mapping"]
        for path, evidence in value["files"].items():
            plan["claims"][path] = evidence["file_key"][:2]
            plan["file_keys"][path] = evidence["file_key"]
        with database.transaction(db):
            db.execute(
                "UPDATE publication_locations SET state='published',"
                "entry_note_path=?,package_root=?,"
                "revision=revision+1,value=? "
                "WHERE kind=? AND publication_id=? AND revision=?",
                (
                    value["target_path"],
                    value["package_root"],
                    canonical(plan),
                    value["kind"],
                    value["publication_id"],
                    value["location_revision"],
                ),
            )
            db.execute("UPDATE vault_operations SET state='completed' WHERE id=?", (operation,))
    except BaseException:
        db.execute("UPDATE vault_operations SET state='reconcile' WHERE id=?", (operation,))
        raise
    from .note_search_jobs import record

    record(config, vault, "pending", reason="document_publication")
    return result(operation, "completed", value)
