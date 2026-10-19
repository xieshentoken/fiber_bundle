"""Explicit, durable delivery of historical command receipts; no action dispatch."""

import asyncio
import json
from collections.abc import Awaitable, Callable

from . import database
from . import weixin_inbox as inbox
from .config import Config, IntegrityError, InvalidInput
from .objects import digest
from .weixin_events import Binding


def initialize(config: Config, binding: Binding) -> None:
    with inbox.connection(config, binding) as db, database.transaction(db):
        inbox.bound(db, binding)
        db.execute(
            "CREATE TABLE IF NOT EXISTS wx_receipt_deliveries("
            "job_id TEXT PRIMARY KEY REFERENCES events(job_id),account_id TEXT NOT NULL,"
            "receipt_sha256 TEXT NOT NULL,body TEXT NOT NULL,"
            "state TEXT NOT NULL CHECK(state IN ('pending','sending','sent','unknown'))) STRICT"
        )


def prepare(config: Config, binding: Binding, job_id: str) -> dict[str, str]:
    initialize(config, binding)
    with inbox.connection(config, binding) as db, database.transaction(db):
        inbox.bound(db, binding)
        receipt = db.execute(
            "SELECT c.result_json,e.raw,e.raw_sha256 FROM wx_command_receipts c JOIN events e "
            "ON c.account_id=e.account_id AND c.event_id=e.event_id "
            "WHERE c.account_id=? AND e.job_id=? AND c.kind='command'",
            (binding.account_id, job_id),
        ).fetchone()
        if receipt is None:
            raise InvalidInput("command receipt unavailable")
        if digest(bytes(receipt["raw"])) != receipt["raw_sha256"]:
            raise IntegrityError("receipt source digest mismatch")
        fingerprint = digest(receipt["result_json"].encode())
        saved = db.execute(
            "SELECT * FROM wx_receipt_deliveries WHERE account_id=? AND job_id=?",
            (binding.account_id, job_id),
        ).fetchone()
        if saved is not None:
            if saved["receipt_sha256"] != fingerprint:
                raise IntegrityError("command receipt changed")
            return {"job_id": job_id, "text": saved["body"], "state": saved["state"]}
        result = json.loads(receipt["result_json"])
        if result.get("state") == "accepted":
            reply = result["reply_text"]
        elif result.get("state") == "rejected":
            reply = "未接受该命令，请核对批次编号、完整命令及资料状态。"
        else:
            raise InvalidInput("command receipt incomplete")
        body = f"操作回执 {job_id}（该命令处理时）：\n{reply}"
        db.execute(
            "INSERT INTO wx_receipt_deliveries VALUES(?,?,?,?,'pending')",
            (job_id, binding.account_id, fingerprint, body),
        )
        return {"job_id": job_id, "text": body, "state": "pending"}


async def deliver(
    config: Config,
    binding: Binding,
    job_id: str,
    sender: Callable[[str, str], Awaitable[bool]],
) -> dict[str, str]:
    prepared = prepare(config, binding, job_id)
    with inbox.connection(config, binding) as db, database.transaction(db):
        inbox.bound(db, binding)
        row = db.execute(
            "SELECT state FROM wx_receipt_deliveries WHERE account_id=? AND job_id=?",
            (binding.account_id, job_id),
        ).fetchone()
        if row["state"] != "pending":
            return {"job_id": job_id, "state": row["state"]}
        db.execute("UPDATE wx_receipt_deliveries SET state='sending' WHERE job_id=?", (job_id,))
    state = "unknown"
    try:
        async with asyncio.timeout(30):
            if await sender(binding.owner_id, prepared["text"]) is True:
                state = "sent"
    except asyncio.CancelledError:
        raise  # Persisted sending cannot safely be retried.
    except Exception:
        pass  # Unknown external outcome; never retain third-party exception text.
    with inbox.connection(config, binding) as db, database.transaction(db):
        inbox.bound(db, binding)
        db.execute(
            "UPDATE wx_receipt_deliveries SET state=? WHERE job_id=? AND state='sending'",
            (state, job_id),
        )
    return {"job_id": job_id, "state": state}
