"""Discover ready result batches; never submit tasks, execute models or prepare stale receipts."""

from . import weixin_inbox as inbox
from . import weixin_task_results as results
from .config import Config
from .weixin_delivery_state import ensure_resolved
from .weixin_events import Binding


def next_batch(config: Config, binding: Binding) -> str | None:
    with inbox.connection(config, binding) as db:
        inbox.bound(db, binding)
        ensure_resolved(db, binding)
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='wx_task_bindings'").fetchone():
            return None
        delivered = set()
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='wx_task_deliveries'").fetchone():
            delivered = {
                r[0]
                for r in db.execute(
                    "SELECT batch_id FROM wx_task_deliveries WHERE account_id=? AND state='sent'",
                    (binding.account_id,),
                )
            }
        candidates = db.execute(
            "SELECT b.id FROM wx_batches b JOIN wx_store_handoffs h ON h.batch_id=b.id "
            "WHERE b.account_id=? AND b.state='selected' AND h.state='stored' "
            "AND EXISTS(SELECT 1 FROM wx_task_bindings t WHERE t.batch_id=b.id "
            "AND t.account_id=b.account_id) ORDER BY b.rowid",
            (binding.account_id,),
        ).fetchall()
        for row in candidates:
            if row["id"] in delivered:
                continue
            try:
                results.snapshot_locked(config, db, binding, row["id"])
            except results.ResultPending:
                continue
            return str(row["id"])
        return None
