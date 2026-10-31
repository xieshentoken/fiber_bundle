"""Current-content repair previews; explicit confirmation uses the existing CAS journal."""

import base64
import json
import time
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from .. import (
    auth,
    database,
    note_identity_store,
    note_index,
    note_inventory,
    note_placement,
    publication_file_io,
)
from .. import note_placement_execution as execution
from ..config import IntegrityError, InvalidInput, StorageError, contained
from ..inputs import canonical
from ..objects import digest
from . import maintenance_deferred_links as deferred
from . import packages, store


def scope(config: Any, db: Any, job: Any) -> tuple[Any, Any, Any]:
    root, allowed = packages.context(config, db, job["client_id"], job["vault_id"], write=True)
    epoch = db.execute(
        "SELECT revision FROM graph_permission_epochs WHERE client_id=? AND vault_id=?",
        (job["client_id"], job["vault_id"]),
    ).fetchone()
    if (
        job["state"] != "completed"
        or [root.stat().st_dev, root.stat().st_ino] != job["fixed"]["root_key"]
    ):
        raise IntegrityError("maintenance_link_repair_task_not_ready")
    return (
        root,
        allowed,
        {
            "root_key": job["fixed"]["root_key"],
            "scope": allowed.wire(),
            "permission_epoch": epoch[0] if epoch else 0,
        },
    )


def current_moves(
    config: Any, db: Any, job: Any, rows: Any, root: Any, allowed: Any
) -> dict[str, str]:
    registered = {r.identity: r for r in note_identity_store.records(config, job["vault_id"])}
    moves: dict[str, str] = {}
    for _, value in rows:
        original = value["source_root"]
        target = value["target_root"]
        entity = value["publication_id"]
        if entity in registered:
            target = registered[entity].relative_path
        elif str(entity).startswith("expert:"):
            package = store.get(config, "packages", entity.removeprefix("expert:"))
            if package:
                target = package["path"]
        for old, recorded in value["moves"].items():
            new = target + recorded[len(value["target_root"]) :]
            if not allowed.allows(old) or not allowed.allows(new):
                raise IntegrityError("maintenance_link_repair_scope_changed")
            if contained(root, old).exists() or contained(root, old).is_symlink():
                raise IntegrityError("maintenance_link_repair_old_path_reused")
            info = contained(root, new).lstat()
            expected = value["source_files"][old]["file_key"][:2]
            if old in value["exchanges"]:
                expected = value["exchanges"][old]["after_key"]
            reg = registered.get(entity) if old == original else None
            if reg and reg.relative_path == new:
                expected = list(reg.file_key[:2])
            if [info.st_dev, info.st_ino] != expected:
                raise IntegrityError("maintenance_link_repair_destination_changed")
            if old in moves and moves[old] != new:
                raise IntegrityError("maintenance_link_repair_mapping_ambiguous")
            moves[old] = new
    return moves


def public(operation: str, state: str, value: Any) -> Any:
    return {
        "operation_id": operation,
        "state": state,
        "plan_sha256": value["plan_sha256"],
        "remaining": value["unavailable"],
        "checked_paths": list(value["observations"]),
        "file_mapping": value["link_moves"],
        "file_write_performed": state == "completed",
        "changes": [
            {
                "path": u["path"],
                "before": base64.b64decode(u["before"]).decode("utf-8-sig"),
                "after": base64.b64decode(u["after"]).decode("utf-8-sig"),
            }
            for u in value["updates"]
        ],
    }


def preview(config: Any, db: Any, job: Any) -> Any:
    root, allowed, fixed = scope(config, db, job)
    for pending in db.execute(
        "SELECT id,state,payload FROM vault_operations WHERE vault_id=? "
        "AND kind='maintenance_link_repair' AND state IN ('applying','reconcile')",
        (job["vault_id"],),
    ):
        saved = json.loads(pending["payload"])
        if saved["job"] == job["id"] and saved["client_id"] == job["client_id"]:
            return public(pending["id"], pending["state"], saved)
    note_placement.guard(db, job["vault_id"])
    rows = deferred.records(db, job)
    if not rows:
        raise InvalidInput("maintenance_link_repair_none_pending")
    moves = current_moves(config, db, job, rows, root, allowed)
    sources: dict[str, Any] = {}
    for _, value in rows:
        for source in value["deferred_links"]["sources"]:
            if source["path"] not in value["deferred_links"]["checked"]:
                old = sources.setdefault(source["path"], source)
                if old != source:
                    raise IntegrityError("maintenance_link_repair_source_ambiguous")
    notes, observations, unavailable = [], {}, []
    registered = {r.identity: r for r in note_identity_store.records(config, job["vault_id"])}
    for path, evidence in sources.items():
        if not allowed.allows(path):
            raise auth.Denied(404)
        try:
            note = note_index.read_note(root, path, note_index.Limits().note_bytes)
        except (StorageError, OSError):
            unavailable.append(path)
            continue
        reg = registered.get(evidence["identity"])
        expected = list(reg.file_key) if reg and reg.relative_path == path else evidence["file_key"]
        if list(note.file_key or ()) != expected:
            raise IntegrityError("maintenance_link_repair_source_changed")
        notes.append(note)
        observations[path] = {"sha256": note.sha256, "file_key": list(note.file_key or ())}
    inventory = note_inventory.Inventory(root, notes, {}, {}, {})
    updates, unresolved = note_placement.plan_links(
        root, allowed, inventory, moves, affected_only=True
    )
    if unresolved:
        raise IntegrityError("maintenance_archive_link_review_required")
    value = {
        "client_id": job["client_id"],
        "vault_id": job["vault_id"],
        "job": job["id"],
        "fixed": fixed,
        "archive_binding": deferred.binding(rows),
        "link_moves": moves,
        "observations": observations,
        "unavailable": unavailable,
        "updates": updates,
        "registrations": [asdict(r) for r in registered.values()],
        "moves": {},
        "move_applied": False,
        "exchanges": {},
        "action": "associate_only",
        "kind": "maintenance_link_repair",
        "publication_id": job["id"],
        "source_root": "",
        "target_root": "",
        "expires_at": time.time() + 900,
    }
    value["plan_sha256"] = digest(canonical(value).encode())
    operation = str(uuid4())
    db.execute(
        "INSERT INTO vault_operations VALUES(?,?,'maintenance_link_repair','prepared',?,?,?)",
        (operation, job["vault_id"], canonical(value), time.time(), time.time()),
    )
    return public(operation, "prepared", value)


def apply(config: Any, db: Any, job: Any, operation: str, expected: str) -> Any:
    root, allowed, fixed = scope(config, db, job)
    row = db.execute(
        "SELECT * FROM vault_operations WHERE id=? AND vault_id=? "
        "AND kind='maintenance_link_repair'",
        (operation, job["vault_id"]),
    ).fetchone()
    if not row:
        raise auth.Denied(404)
    value = json.loads(row["payload"])
    if value["client_id"] != job["client_id"] or value["job"] != job["id"]:
        raise auth.Denied(404)
    frozen = {k: v for k, v in value.items() if k != "plan_sha256"}
    frozen["exchanges"] = {}
    if (
        expected != value["plan_sha256"]
        or value["fixed"] != fixed
        or digest(canonical(frozen).encode()) != expected
    ):
        raise IntegrityError("maintenance_link_repair_preview_changed")
    if row["state"] == "completed":
        finalize(config, db, job, value)
        return public(operation, "completed", value)
    if row["state"] not in {"prepared", "applying", "reconcile"}:
        raise InvalidInput("maintenance_link_repair_not_ready")
    rows = deferred.records(db, job)
    if deferred.binding(rows) != value["archive_binding"]:
        raise IntegrityError("maintenance_link_repair_preview_changed")
    if current_moves(config, db, job, rows, root, allowed) != value["link_moves"]:
        raise IntegrityError("maintenance_link_repair_destination_changed")
    if row["state"] == "prepared":
        note_placement.guard(db, job["vault_id"])
        if time.time() > value["expires_at"] or not value["observations"]:
            raise InvalidInput("maintenance_link_repair_not_ready")
        for path, evidence in value["observations"].items():
            raw, key = publication_file_io.read(root, path)
            if digest(raw) != evidence["sha256"] or key != evidence["file_key"]:
                raise IntegrityError("maintenance_link_repair_source_changed")
    if row["state"] != "prepared":
        changed = {u["path"] for u in value["updates"]}
        for path, evidence in value["observations"].items():
            if path not in changed:
                raw, key = publication_file_io.read(root, path)
                if digest(raw) != evidence["sha256"] or key != evidence["file_key"]:
                    raise IntegrityError("maintenance_link_repair_source_changed")
    execution.persist(db, operation, value, "applying")
    try:
        for update in value["updates"]:
            execution.exchange(config, db, operation, value, root, allowed, update)
        execution.complete(config, db, operation, value, root)
        finalize(config, db, job, value)
    except BaseException:
        execution.persist(db, operation, value, "reconcile")
        raise
    return public(operation, "completed", value)


def finalize(config: Any, db: Any, job: Any, repair: Any) -> None:
    # Idempotent even if a crash follows file exchange or the completed marker.
    with database.transaction(db):
        for oid, value in deferred.records(db, job):
            pending = value["deferred_links"]
            pending["checked"] = sorted(set(pending["checked"]) | set(repair["observations"]))
            if all(s["path"] in pending["checked"] for s in pending["sources"]):
                pending["state"] = "verified"
            execution.persist(db, oid, value, "completed")
    remaining = {oid for oid, _ in deferred.records(db, job)}
    for item in job["items"]:
        if item.get("archive_operation"):
            item["links_pending"] = item["archive_operation"] in remaining
    store.put(config, "jobs", job["id"], job["vault_id"], job)
