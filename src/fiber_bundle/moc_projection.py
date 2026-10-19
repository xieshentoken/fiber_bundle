"""Refresh one managed MOC, retaining missing and human-edited files as exceptions."""

import sqlite3
from typing import Any

from . import auth, moc_file_identity, moc_file_journal, moc_files, moc_store, note_inventory
from .config import Config, IntegrityError


def refresh(
    config: Config, db: sqlite3.Connection, client: str, vid: str, mid: str, revision: int
) -> dict[str, Any]:
    auth.grant(db, client, vid, "add")
    vault = auth.grant(db, client, vid, "query")
    record = moc_store.load(config, vid, mid)
    if record is None or record.revision != revision:
        raise IntegrityError("MOC projection revision changed")
    history = moc_file_journal.plans(config, vid, mid)
    pending = [p for p in history if p.status == "pending"]
    if pending:
        return {"status": "reconciliation_required", "plan_id": pending[0].plan_id}
    prior = moc_file_journal.latest(history)
    if prior is None:
        return {"status": "metadata_only"}
    inventory = note_inventory.read(config, db, vault)
    pair = moc_file_identity.transition(prior.value)
    if pair is not None:
        note = next((n for n in inventory.notes if n.identity == pair[1].identity), None)
    else:
        note = next((n for n in inventory.notes if n.relative_path == prior.value["path"]), None)
    if note is None:
        return {"status": "missing_preserved"}
    planned = moc_files.plan_file(
        config, db, client, vid, mid, revision, note.relative_path, note.sha256
    )
    if planned["plan_id"] is None:
        return {"status": "unchanged", "path": note.relative_path}
    return moc_files.apply_file(config, db, client, vid, mid, planned["plan_id"])
