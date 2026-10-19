"""Explicit adoption of existing MOC files through the existing guarded write journal."""

import json
import sqlite3
import time
from dataclasses import asdict
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from . import (
    auth,
    moc_file_journal,
    moc_files,
    moc_markdown,
    moc_store,
    note_inventory,
    note_placement,
    vault_directories,
    vault_save_policy,
)
from .config import Config, IntegrityError, InvalidInput
from .inputs import canonical
from .objects import digest


def guard(db: sqlite3.Connection, vault: str) -> None:
    if db.execute(
        "SELECT 1 FROM vault_operations WHERE vault_id=? AND kind='moc_registration' "
        "AND state IN ('applying','reconcile')",
        (vault,),
    ).fetchone():
        raise IntegrityError("moc_registration_requires_recovery")


def adoption_context(
    config: Config, db: sqlite3.Connection, client: str, vault: str
) -> tuple[Any, Any, dict[str, Any]]:
    for permission in ("query", "add"):
        auth.grant(db, client, vault, permission)
    root, scope, _ = vault_directories.vault_root(config, db, vault)
    policy = vault_save_policy.get(config, db, vault)
    # Adopting an existing Map does not publish new material. Its own file and
    # root evidence guards apply even when an unrelated directory binding is old.
    if policy["state"] != "active" or policy["settings"]["layout_mode"] != "obsidian_folders":
        raise InvalidInput("placement_active_policy_required")
    return root, scope, policy


def context(config: Config, db: sqlite3.Connection, client: str, vault: str) -> tuple[Any, Any]:
    _, _, policy = adoption_context(config, db, client, vault)
    inventory = note_inventory.read(config, db, auth.grant(db, client, vault, "query"))
    return inventory, policy


def bindings(config: Config, vault: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for mid in note_placement.records(config, vault):
        plans = moc_file_journal.plans(config, vault, mid)
        prior = moc_file_journal.latest(plans)
        for plan in ([prior] if prior else []) + [p for p in plans if p.status == "pending"]:
            if plan.value["path"] in result and result[plan.value["path"]] != mid:
                raise IntegrityError("moc_registration_duplicate_binding")
            result[plan.value["path"]] = mid
    return result


def listing(config: Config, db: sqlite3.Connection, client: str, vault: str) -> dict[str, Any]:
    adoption_context(config, db, client, vault)
    operations = [
        {"id": r["id"], "state": r["state"], "path": json.loads(r["payload"])["path"]}
        for r in db.execute(
            "SELECT id,state,payload FROM vault_operations WHERE vault_id=? "
            "AND kind='moc_registration' AND state IN ('applying','reconcile')",
            (vault,),
        )
        if json.loads(r["payload"])["client_id"] == client
    ]
    try:
        inventory, policy = context(config, db, client, vault)
    except IntegrityError:
        if operations:
            return {"candidates": [], "truncated": False, "operations": operations}
        raise
    bound = bindings(config, vault)
    prefix = policy["settings"].get("moc_dir", "Map") + "/"
    candidates = [
        {
            "path": n.relative_path,
            "title": n.title,
            "sha256": n.sha256,
            "moc_id": bound.get(n.relative_path),
        }
        for n in inventory.notes
        if n.relative_path.startswith(prefix)
    ]
    return {
        "candidates": candidates[:1000],
        "truncated": len(candidates) > 1000,
        "operations": operations,
    }


def observe(
    config: Config, db: sqlite3.Connection, client: str, vault: str, path: str, expected: str
) -> tuple[dict[str, Any], bytes, Any]:
    inventory, policy = context(config, db, client, vault)
    prefix = policy["settings"].get("moc_dir", "Map") + "/"
    if not path.startswith(prefix):
        raise InvalidInput("moc_registration_outside_directory")
    note = next((n for n in inventory.notes if n.relative_path == path), None)
    if note is None or note.publication_id is not None or note.sha256 != expected:
        raise IntegrityError("moc_registration_file_changed")
    if path in bindings(config, vault):
        raise InvalidInput("moc_registration_already_registered")
    mid = str(uuid5(NAMESPACE_URL, "fiber-moc-adoption:" + vault + ":" + path))
    raw, key = moc_files.target(inventory, path)
    moc_markdown.split(raw, mid)  # Refuse foreign or malformed managed blocks.
    if b"<!-- fiber-moc:" in raw:
        raise IntegrityError("moc_registration_foreign_block")
    key = vault_directories.key(inventory.root)
    record = moc_store.Record(
        note.title, key[0], key[1], moc_store.next_revision(config, vault, mid), {}
    )
    moc_store.validate(record)
    return (
        {
            "client_id": client,
            "vault_id": vault,
            "moc_id": mid,
            "path": path,
            "before_sha256": expected,
            "file_key": key,
            "root_key": vault_directories.key(inventory.root),
            "policy_revision": policy["revision"],
            "record": asdict(record),
        },
        raw,
        inventory,
    )


def preview(
    config: Config, db: sqlite3.Connection, client: str, vault: str, path: str, expected: str
) -> dict[str, Any]:
    note_placement.guard(db, vault)
    guard(db, vault)
    value, raw, inventory = observe(config, db, client, vault, path, expected)
    record = moc_store.Record(**value["record"])
    after, _, _ = moc_markdown.render(raw, value["moc_id"], path, record, inventory)
    value.update(after_sha256=digest(after), expires_at=time.time() + 900, plan_id=None)
    operation = str(uuid4())
    now = time.time()
    db.execute(
        "INSERT INTO vault_operations VALUES(?,?,'moc_registration','prepared',?,?,?)",
        (operation, vault, canonical(value), now, now),
    )
    return {
        "operation_id": operation,
        "path": path,
        "title": record.title,
        "markdown": after.decode("utf-8-sig"),
        "manual_bytes_preserved": len(raw),
        "moc_id": value["moc_id"],
        "file_write_performed": False,
    }


def persist(db: sqlite3.Connection, operation: str, value: dict[str, Any], state: str) -> None:
    db.execute(
        "UPDATE vault_operations SET payload=?,state=?,updated_at=? WHERE id=?",
        (canonical(value), state, time.time(), operation),
    )


def apply(
    config: Config, db: sqlite3.Connection, client: str, vault: str, operation: str, action: str
) -> dict[str, Any]:
    for permission in ("query", "add"):
        auth.grant(db, client, vault, permission)
    row = db.execute(
        "SELECT * FROM vault_operations WHERE id=? AND vault_id=? AND kind='moc_registration'",
        (operation, vault),
    ).fetchone()
    if not row or json.loads(row["payload"])["client_id"] != client:
        raise auth.Denied(404)
    value = json.loads(row["payload"])
    if action == "cancel":
        if row["state"] != "prepared":
            raise IntegrityError("moc_registration_requires_recovery")
        persist(db, operation, value, "cancelled")
        return {"state": "cancelled"}
    if row["state"] == "completed":
        return {"state": "completed", "moc_id": value["moc_id"]}
    if row["state"] == "prepared":
        if action != "apply" or time.time() > value["expires_at"]:
            raise IntegrityError("moc_registration_preview_expired")
        note_placement.guard(db, vault)
        observed, _, _ = observe(config, db, client, vault, value["path"], value["before_sha256"])
        if any(canonical(v) != canonical(value[k]) for k, v in observed.items()):
            raise IntegrityError("moc_registration_file_changed")
    elif row["state"] not in {"applying", "reconcile"} or action != "reconcile":
        raise IntegrityError("moc_registration_requires_recovery")
    root, _, policy = adoption_context(config, db, client, vault)
    if (
        vault_directories.key(root) != value["root_key"]
        or policy["revision"] != value["policy_revision"]
    ):
        raise IntegrityError("moc_registration_file_changed")
    # Another unfinished registration/placement must be recovered first.
    if db.execute(
        "SELECT 1 FROM vault_operations WHERE vault_id=? AND id<>? "
        "AND kind IN ('moc_registration','note_placement','layout_migration','publication_rebind') "
        "AND state IN ('applying','reconcile')",
        (vault, operation),
    ).fetchone():
        raise IntegrityError("placement_reconciliation_required")
    persist(db, operation, value, "applying")
    try:
        record = moc_store.Record(**value["record"])
        current = moc_store.load(config, vault, value["moc_id"])
        if current is None:
            moc_store.save(config, vault, value["moc_id"], client, record, "adopt-existing", 0)
        elif current != record:
            raise IntegrityError("moc_registration_file_changed")
        if value["plan_id"] is None:
            packet = moc_files.plan_file(
                config,
                db,
                client,
                vault,
                value["moc_id"],
                record.revision,
                value["path"],
                value["before_sha256"],
            )
            if packet["after_sha256"] != value["after_sha256"]:
                raise IntegrityError("moc_registration_file_changed")
            value["plan_id"] = packet["plan_id"]
            persist(db, operation, value, "applying")
        plans = moc_file_journal.plans(config, vault, value["moc_id"])
        plan = next(p for p in plans if p.plan_id == value["plan_id"])
        result = moc_files.apply_file(
            config,
            db,
            client,
            vault,
            value["moc_id"],
            plan.plan_id,
            reconcile=plan.status == "pending",
        )
        if result["status"] == "not-applied":
            value["plan_id"] = None
            persist(db, operation, value, "reconcile")
            return {"state": "reconcile", "reason": "moc_registration_not_applied"}
        persist(db, operation, value, "completed")
        from .note_search_jobs import record as refresh_index

        refresh_index(config, vault, "pending", reason="document_publication")
        return {"state": "completed", "moc_id": value["moc_id"], "path": value["path"]}
    except Exception:
        persist(db, operation, value, "reconcile")
        raise
