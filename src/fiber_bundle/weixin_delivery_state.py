"""Shared account-level barrier for uncertain outbound effects."""

import sqlite3

from .config import InvalidInput
from .weixin_events import Binding


def ensure_resolved(db: sqlite3.Connection, binding: Binding) -> None:
    """Uncertain result delivery also blocks the account's automatic outbound queue."""
    for table in (
        "wx_confirmation_prompts",
        "wx_batch_reminders",
        "wx_receipt_deliveries",
        "wx_task_deliveries",
        "wx_media_terminology_prompts",
    ):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
            continue
        if db.execute(
            f"SELECT 1 FROM {table} WHERE account_id=? AND state IN ('sending','unknown') LIMIT 1",
            (binding.account_id,),
        ).fetchone():
            raise InvalidInput("outbound account has unresolved delivery")
