"""Administrator receipt feedback is independent evidence; UNKNOWN barriers stay intact."""

import time
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Request

from . import admin_batches, auth, database
from .config import Config, InvalidInput
from .payloads import Payload
from .query_http import access

TABLES = {
    "wx_task_deliveries": "batch_id",
    "wx_confirmation_prompts": "id",
    "wx_batch_reminders": "id",
    "wx_receipt_deliveries": "job_id",
    "wx_media_terminology_prompts": "id",
}


def listing(config: Config) -> list[dict[str, Any]]:
    output = []
    with admin_batches.journal(config) as db:
        if db is None:
            return []
        for table, key in TABLES.items():
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                continue
            for row in db.execute(
                f"SELECT {key},account_id,state FROM {table} "
                "WHERE state IN ('unknown','sending') LIMIT 100"
            ):
                feedback = None
                if db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='fiber_delivery_feedback'"
                ).fetchone():
                    saved = db.execute(
                        "SELECT outcome FROM fiber_delivery_feedback "
                        "WHERE ledger=? AND delivery_id=? ORDER BY rowid DESC LIMIT 1",
                        (table, row[0]),
                    ).fetchone()
                    feedback = saved[0] if saved else None
                output.append(
                    {
                        "ledger": table,
                        "delivery_id": row[0],
                        "state": row[2],
                        "platform": "feishu" if row[1].startswith("feishu:") else "weixin",
                        "user_feedback": feedback,
                        "protocol_ack": "unknown",
                        "automatic_retry": False,
                    }
                )
    return output


def record(config: Config, ledger: str, identity: str, outcome: str) -> dict[str, str]:
    if ledger not in TABLES or outcome not in {"received", "not_received", "unsure"}:
        raise InvalidInput("invalid delivery feedback")
    with admin_batches.journal(config, write=True) as db:
        if (
            db is None
            or not db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (ledger,)).fetchone()
        ):
            raise auth.Denied(404)
        row = db.execute(
            f"SELECT state FROM {ledger} WHERE {TABLES[ledger]}=?", (identity,)
        ).fetchone()
        if row is None or row[0] not in {"unknown", "sending"}:
            raise auth.Denied(404)
        with database.transaction(db):
            db.execute(
                "CREATE TABLE IF NOT EXISTS fiber_delivery_feedback("
                "id TEXT PRIMARY KEY,ledger TEXT NOT NULL,delivery_id TEXT NOT NULL,"
                "outcome TEXT NOT NULL,created_at REAL NOT NULL) STRICT"
            )
            db.execute(
                "INSERT INTO fiber_delivery_feedback VALUES(?,?,?,?,?)",
                (str(uuid4()), ledger, identity, outcome, time.time()),
            )
        return {"state": row[0], "user_feedback": outcome, "protocol_ack": "unknown"}


class Feedback(Payload):
    ledger: str
    delivery_id: str
    outcome: Literal["received", "not_received", "unsure"]


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/admin/delivery-reconciliation")

    @routes.get("")
    def get(request: Request) -> list[dict[str, Any]]:
        with access(config, request, admin=True):
            return listing(config)

    @routes.post("")
    def post(data: Feedback, request: Request) -> dict[str, str]:
        with access(config, request, admin=True):
            return record(config, data.ledger, data.delivery_id, data.outcome)

    return routes
