"""Project committed MOC membership and controlled archive links into existing MOC files."""

import base64
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import (
    auth,
    moc_file_identity,
    moc_file_journal,
    moc_files,
    moc_markdown,
    moc_store,
    note_inventory,
)
from .config import Config, IntegrityError, contained
from .objects import digest
from .publication_file_io import read


def prepare(
    config: Config,
    vault: str,
    inventory: note_inventory.Inventory,
    mocs: dict[str, Any],
    association: dict[str, Any] | None,
    moves: dict[str, str],
    updates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    virtual = replace(
        inventory,
        notes=[
            replace(n, relative_path=moves.get(n.relative_path, n.relative_path))
            for n in inventory.notes
        ],
    )
    affected = {n.identity for n in inventory.notes if n.relative_path in moves}
    specs = []
    for mid, old_record in mocs.items():
        if not old_record or not (
            (association and association["moc_id"] == mid)
            or affected.intersection(old_record.members)
        ):
            continue
        plans = moc_file_journal.plans(config, vault, mid)
        if any(p.status == "pending" for p in plans):
            raise IntegrityError("MOC file has a pending write; reconcile first")
        prior = moc_file_journal.latest(plans)
        if prior is None:
            continue
        path = prior.value["path"]
        raw, key = read(inventory.root, path)
        _, block, _ = moc_markdown.split(raw, mid)
        if digest(block) != prior.value["managed_sha256"]:
            raise IntegrityError("MOC managed block missing or human edited")
        record = (
            moc_store.Record(**association["after"])
            if association and association["moc_id"] == mid
            else old_record
        )
        existing = next((u for u in updates if u["path"] == path), None)
        source = base64.b64decode(existing["after"]) if existing else raw
        after, new_block, preserved = moc_markdown.render(source, mid, path, record, virtual)
        if after == raw:
            continue
        identity = moc_file_identity.capture(config, vault, inventory, path)
        spec = {
            "moc_id": mid,
            "path": path,
            "revision": record.revision,
            "base_id": prior.plan_id,
            "before_sha256": digest(raw),
            "after_sha256": digest(after),
            "file_key": key,
            "identity": identity,
            "managed_sha256": digest(new_block),
            "manual_links_preserved": preserved,
        }
        specs.append(spec)
        update = {
            "path": path,
            "target": path,
            "before_sha256": digest(raw),
            "after_sha256": digest(after),
            "file_key": key,
            "before": base64.b64encode(raw).decode(),
            "after": base64.b64encode(after).decode(),
        }
        if existing:
            updates[updates.index(existing)] = update
        else:
            updates.append(update)
    return specs


def finalize(
    config: Config, db: sqlite3.Connection, operation: str, value: dict[str, Any], root: Path
) -> None:
    specs = value.get("moc_file_projections", [])
    if not specs:
        return
    inventory = note_inventory.read(
        config, db, auth.grant(db, value["client_id"], value["vault_id"], "query")
    )
    for spec in specs:
        mid, path = spec["moc_id"], spec["path"]
        raw, key = read(root, path)
        if digest(raw) != spec["after_sha256"]:
            raise IntegrityError("placement_moc_projection_changed")
        record = moc_store.load(config, value["vault_id"], mid)
        if record is None or record.revision != spec["revision"]:
            raise IntegrityError("placement_moc_changed")
        plans = moc_file_journal.plans(config, value["vault_id"], mid)
        current = moc_file_journal.latest(plans)
        if (
            current
            and current.value["operation"] == operation
            and current.value["after_sha256"] == spec["after_sha256"]
        ):
            continue
        if current is None or current.plan_id != spec["base_id"]:
            raise IntegrityError("placement_moc_projection_changed")
        info = root.stat()
        parent = contained(root, path).parent.stat()
        packet = {
            k: spec[k]
            for k in [
                "moc_id",
                "path",
                "revision",
                "base_id",
                "before_sha256",
                "after_sha256",
                "file_key",
                "managed_sha256",
                "manual_links_preserved",
            ]
        }
        packet["file_key"] = spec["file_key"][:2]
        packet.update(
            schema=1,
            operation=operation,
            vault_id=value["vault_id"],
            vault_key=[info.st_dev, info.st_ino],
            parent_key=[parent.st_dev, parent.st_ino],
            inventory_sha256=moc_files.observed(inventory, path),
        )
        if spec["identity"] is not None:
            note = next(n for n in inventory.notes if n.relative_path == path)
            packet.update(
                schema=2, identity=spec["identity"], after_file_key=list(note.file_key or ())
            )
        update = next(u for u in value["updates"] if u["path"] == path)
        moc_file_journal.record_applied(config, packet, base64.b64decode(update["before"]))


def check(config: Config, value: dict[str, Any], operation: str) -> None:
    for spec in value.get("moc_file_projections", []):
        plans = moc_file_journal.plans(config, value["vault_id"], spec["moc_id"])
        latest = moc_file_journal.latest(plans)
        if (
            any(
                p.status == "pending"
                and (
                    p.value["operation"] != operation
                    or p.value["after_sha256"] != spec["after_sha256"]
                )
                for p in plans
            )
            or latest is None
            or (latest.plan_id != spec["base_id"] and latest.value["operation"] != operation)
        ):
            raise IntegrityError("placement_moc_projection_changed")
