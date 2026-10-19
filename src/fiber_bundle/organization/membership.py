"""Apply membership through Fiber's existing ownership ledger and atomic MOC journal."""

import sqlite3
from dataclasses import replace
from typing import Any

from .. import (
    auth,
    moc_file_journal,
    moc_files,
    moc_registration,
    moc_store,
)
from ..config import Config, IntegrityError, InvalidInput
from ..objects import digest
from . import note_moc_sync, packages, store


def decision_key(vault: str, entity: str, moc: str) -> str:
    return vault + ":" + entity + ":" + moc


def project(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vault: str,
    moc: Any,
    *,
    missing_block_repair: tuple[str, str] | None = None,
) -> bool:
    wrote = False
    # A crash after metadata commit is reconciled before generating another file plan.
    for pending in moc_file_journal.plans(config, vault, moc["id"]):
        if pending.status == "pending":
            wrote = True
            moc_files.apply_file(
                config, db, client, vault, moc["id"], pending.plan_id, reconcile=True
            )
    current = moc_store.load(config, vault, moc["id"])
    if current is None:
        raise IntegrityError("organization_moc_not_registered")
    root, allowed = packages.context(config, db, client, vault, write=True)
    data = packages.scoped_read(root, allowed, moc["path"])
    plan = moc_files.plan_file(
        config,
        db,
        client,
        vault,
        moc["id"],
        current.revision,
        moc["path"],
        digest(data),
        missing_block_repair=missing_block_repair,
    )
    if plan.get("plan_id"):
        moc_files.apply_file(config, db, client, vault, moc["id"], plan["plan_id"])
    return wrote or bool(plan.get("plan_id"))


def apply(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vault: str,
    entity: Any,
    moc: Any,
    *,
    owner: str = "system",
    action: str = "link",
    record_decision: bool = True,
    missing_block_repair: tuple[str, str] | None = None,
) -> Any:
    root, allowed = packages.context(config, db, client, vault, write=True)
    if not allowed.allows(entity["path"]) or not allowed.allows(moc["path"]):
        raise auth.Denied(404)
    key = decision_key(vault, entity["id"], moc["id"])
    prior = store.get(config, "decisions", key)
    if action == "unlock":
        store.put(
            config,
            "decisions",
            key,
            vault,
            {"entity": entity["id"], "moc": moc["id"], "state": "unlocked"},
        )
        return False
    if owner == "system" and prior and prior["state"] in {"rejected", "pinned", "retired"}:
        return False
    note_moc_sync.check(config, db, client, vault, entity)
    note_moc_sync.track(config, db, client, vault, entity)
    current = moc_store.load(config, vault, moc["id"])
    if current is None:
        if action != "link":
            raise InvalidInput("organization_moc_not_registered")
        preview = moc_registration.preview(config, db, client, vault, moc["path"], moc["sha256"])
        moc_registration.apply(config, db, client, vault, preview["operation_id"], "apply")
        current = moc_store.load(config, vault, moc["id"])
    if current is None:
        raise IntegrityError("organization_moc_not_registered")
    info = root.stat()
    if (current.vault_dev, current.vault_ino) != (info.st_dev, info.st_ino):
        raise IntegrityError("organization_vault_changed")
    if owner == "manual" and record_decision:
        store.put(
            config,
            "decisions",
            key,
            vault,
            {
                "entity": entity["id"],
                "moc": moc["id"],
                "state": "pinned" if action == "link" else "rejected",
            },
        )
    members = {k: v.copy() for k, v in current.members.items()}
    if action == "link":
        members[entity["id"]] = sorted(set(members.get(entity["id"], [])) | {owner})
    elif owner == "manual":
        members.pop(entity["id"], None)
    elif entity["id"] in members:
        members[entity["id"]] = [o for o in members[entity["id"]] if o != "system"]
        if not members[entity["id"]]:
            del members[entity["id"]]
    changed = members != current.members
    if changed:
        value = replace(current, members=members, revision=current.revision + 1)
        moc_store.save(
            config, vault, moc["id"], client, value, "organization-" + action, current.revision
        )
    wrote = project(config, db, client, vault, moc, missing_block_repair=missing_block_repair)
    wrote = note_moc_sync.sync(config, db, client, vault, entity) or wrote
    if record_decision and (changed or wrote):
        from .jobs import request_index

        request_index(config, client, vault)
    # Classification intentionally does not enqueue physical archive actions.
    return changed
