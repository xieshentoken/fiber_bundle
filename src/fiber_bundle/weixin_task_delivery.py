"""One explicit batch-result delivery, with persistent claims and send-time authorization."""

import asyncio
from collections.abc import Awaitable, Callable

from . import database
from . import weixin_inbox as inbox
from . import weixin_task_results as results
from .config import Config, IntegrityError, InvalidInput
from .weixin_delivery_state import ensure_resolved
from .weixin_events import Binding

# A trusted transport MUST call the guard after its send gate, immediately before posting.
Sender = Callable[[str, str, Callable[[], None]], Awaitable[bool]]


def prepare(config: Config, binding: Binding, batch_id: str) -> dict[str, str]:
    with inbox.connection(config, binding) as db:
        body, fingerprint = results.snapshot_locked(config, db, binding, batch_id)
        with database.transaction(db):
            db.execute(
                "CREATE TABLE IF NOT EXISTS wx_task_deliveries("
                "batch_id TEXT PRIMARY KEY REFERENCES wx_batches(id),account_id TEXT NOT NULL,"
                "result_sha256 TEXT NOT NULL,body TEXT NOT NULL,"
                "state TEXT NOT NULL CHECK(state IN ('pending','sending','sent','unknown'))) STRICT"
            )
            saved = db.execute(
                "SELECT * FROM wx_task_deliveries WHERE batch_id=?", (batch_id,)
            ).fetchone()
            if saved is not None:
                if (saved["account_id"], saved["result_sha256"], saved["body"]) != (
                    binding.account_id,
                    fingerprint,
                    body,
                ):
                    raise IntegrityError("batch result changed")
                return {"batch_id": batch_id, "state": saved["state"], "text": body}
            db.execute(
                "INSERT INTO wx_task_deliveries VALUES(?,?,?,?,'pending')",
                (batch_id, binding.account_id, fingerprint, body),
            )
        return {"batch_id": batch_id, "state": "pending", "text": body}


def _authorize(config: Config, binding: Binding, batch_id: str, body: str) -> None:
    with inbox.connection(config, binding) as db:
        current, fingerprint = results.snapshot_locked(config, db, binding, batch_id)
        saved = db.execute(
            "SELECT * FROM wx_task_deliveries WHERE batch_id=? AND account_id=?",
            (batch_id, binding.account_id),
        ).fetchone()
        if (
            saved is None
            or saved["state"] != "sending"
            or saved["body"] != body
            or saved["result_sha256"] != fingerprint
            or current != body
        ):
            raise IntegrityError("batch result delivery changed")


async def deliver(
    config: Config, binding: Binding, batch_id: str, sender: Sender
) -> dict[str, str]:
    prepared = prepare(config, binding, batch_id)
    with inbox.connection(config, binding) as db, database.transaction(db):
        inbox.bound(db, binding)
        # Claim under the same shared lock as cancellation. Revalidate again at transport gate.
        current, fingerprint = results.snapshot_locked(config, db, binding, batch_id)
        row = db.execute(
            "SELECT * FROM wx_task_deliveries WHERE batch_id=?", (batch_id,)
        ).fetchone()
        if row["state"] != "pending":
            return {"batch_id": batch_id, "state": row["state"]}
        ensure_resolved(db, binding)
        if current != prepared["text"] or row["result_sha256"] != fingerprint:
            raise IntegrityError("batch result changed before claim")
        db.execute("UPDATE wx_task_deliveries SET state='sending' WHERE batch_id=?", (batch_id,))
    state = "unknown"
    checked = False

    def authorize() -> None:
        nonlocal checked
        if checked:
            raise InvalidInput("result transport must not retry")
        _authorize(config, binding, batch_id, prepared["text"])
        checked = True

    try:
        async with asyncio.timeout(30):
            if await sender(binding.owner_id, prepared["text"], authorize) is True and checked:
                state = "sent"
    except asyncio.CancelledError:
        raise  # sending survives cancellation; it is never automatically retried.
    except Exception:
        pass  # Do not persist exception text or infer that a failed request had no effect.
    with inbox.connection(config, binding) as db, database.transaction(db):
        inbox.bound(db, binding)
        db.execute(
            "UPDATE wx_task_deliveries SET state=? WHERE batch_id=? AND state='sending'",
            (state, batch_id),
        )
    return {"batch_id": batch_id, "state": state}
