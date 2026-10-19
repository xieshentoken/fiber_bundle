"""Fresh writeback evidence, including only this intent's journaled MOC identity exchange."""

import sqlite3

from .. import moc_file_identity, moc_file_journal, note_inventory, vaults
from ..config import Config, IntegrityError


def current(
    config: Config, db: sqlite3.Connection, vault: sqlite3.Row, wid: str, moc_id: str
) -> note_inventory.Inventory:
    plans = [
        p
        for p in moc_file_journal.plans(config, vault["id"], moc_id)
        if p.value.get("graph_writeback_id") == wid
    ]
    if len(plans) > 1:
        raise IntegrityError("graph_moc_reconcile_requires_manual_review")
    projection = None
    if plans:
        plan = plans[0]
        projection = moc_file_identity.projection(
            config,
            vault["id"],
            vaults.checked_vault(config, vault),
            plan.value,
            allow_after=plan.status in {"pending", "applied"},
        )
    # Every source remains a fresh authorized file; stale cached bodies are never used.
    return note_inventory.read(config, db, vault, projection=projection)
