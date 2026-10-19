"""Durable daily batch digest, optional per batch; unknown sends never replay."""

import asyncio
import hashlib
import json
import math
import sqlite3
import time
from typing import Any
from uuid import uuid4

from . import admin_batches, database
from . import weixin_inbox as inbox
from .config import Config, InvalidInput
from .weixin_delivery_state import ensure_resolved
from .weixin_events import Binding
from .weixin_task_delivery import Sender

INTERVAL = 86400
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS wx_batch_reminder_settings("
    "batch_id TEXT PRIMARY KEY REFERENCES wx_batches(id),"
    "enabled INTEGER NOT NULL CHECK(enabled IN (0,1))) STRICT",
    "CREATE TABLE IF NOT EXISTS wx_batch_reminders("
    "id TEXT PRIMARY KEY,account_id TEXT NOT NULL REFERENCES bindings(account_id),"
    "created_at REAL NOT NULL,scope_json TEXT NOT NULL CHECK(json_valid(scope_json)),"
    "body TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('sending','sent','unknown'))) STRICT",
)


def initialize(db: sqlite3.Connection) -> None:
    for sql in SCHEMA:
        db.execute(sql)


def enabled(db: sqlite3.Connection, batch_id: str) -> bool:
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='wx_batch_reminder_settings'"
    ).fetchone():
        return True
    row = db.execute(
        "SELECT enabled FROM wx_batch_reminder_settings WHERE batch_id=?", (batch_id,)
    ).fetchone()
    return row is None or row[0] == 1


def settings(config: Config) -> list[dict[str, Any]]:
    result = admin_batches.pending(config)
    with admin_batches.journal(config) as db:
        for batch in result:
            assert db is not None
            batch["reminder_enabled"] = enabled(db, batch["id"])
    return result


def history(config: Config) -> list[dict[str, Any]]:
    """Read bounded delivery evidence without creating a journal or clearing unknown effects."""
    with admin_batches.journal(config) as db:
        if (
            db is None
            or not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='wx_batch_reminders'"
            ).fetchone()
        ):
            return []
        return [
            {
                "id": row["id"],
                "state": row["state"],
                "created_at": row["created_at"],
                "body_sha256": hashlib.sha256(row["body"].encode()).hexdigest(),
                "batch_count": len(json.loads(row["scope_json"])),
            }
            for row in db.execute(
                "SELECT * FROM wx_batch_reminders ORDER BY created_at DESC,id LIMIT 20"
            )
        ]


def set_enabled(config: Config, batch_id: str, value: bool) -> dict[str, Any]:
    if type(value) is not bool:
        raise InvalidInput("batch_reminder_flag_invalid")
    with admin_batches.journal(config, write=True) as db:
        if (
            db is None
            or not db.execute("SELECT 1 FROM wx_batches WHERE id=?", (batch_id,)).fetchone()
        ):
            raise InvalidInput("unknown_weixin_batch")
        with database.transaction(db):
            initialize(db)
            db.execute(
                "INSERT INTO wx_batch_reminder_settings VALUES(?,?) ON CONFLICT(batch_id) "
                "DO UPDATE SET enabled=excluded.enabled",
                (batch_id, int(value)),
            )
    return {"batch_id": batch_id, "enabled": value}


def due(db: sqlite3.Connection, binding: Binding, now: float) -> list[dict[str, str]]:
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='wx_confirmation_prompts'"
    ).fetchone():
        return []
    rows = db.execute(
        "SELECT b.id,p.id AS prompt_id FROM wx_batches b "
        "JOIN wx_confirmation_prompts p ON p.batch_id=b.id "
        "LEFT JOIN wx_batch_reminder_settings s ON s.batch_id=b.id "
        "WHERE b.account_id=? AND (b.state='frozen' OR (b.state='selected' "
        "AND b.action='consult' AND b.question IS NULL)) "
        "AND b.last_received<=? AND p.state='sent' AND coalesce(s.enabled,1)=1 "
        "ORDER BY b.last_received,b.id LIMIT 1000",
        (binding.account_id, now - INTERVAL),
    ).fetchall()
    return [{"batch_id": r["id"], "prompt_id": r["prompt_id"]} for r in rows]


async def deliver(
    config: Config, binding: Binding, sender: Sender, *, now: float | None = None
) -> dict[str, Any]:
    clock = time.time() if now is None else now
    if type(clock) not in (int, float) or not math.isfinite(clock) or clock <= 0:
        raise InvalidInput("reminder_clock_invalid")
    with inbox.connection(config, binding) as db, database.transaction(db):
        inbox.bound(db, binding)
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='wx_batches'").fetchone():
            return {"state": "idle"}
        initialize(db)
        ensure_resolved(db, binding)
        prior = db.execute(
            "SELECT max(created_at) FROM wx_batch_reminders WHERE account_id=?",
            (binding.account_id,),
        ).fetchone()[0]
        if prior is not None and clock < prior + INTERVAL:
            return {"state": "idle"}
        scope = due(db, binding, clock)
        if not scope:
            return {"state": "idle"}
        identity = str(uuid4())
        body = (
            "待确认批次每日摘要："
            + ("至少 " if len(scope) == 1000 else "")
            + str(len(scope))
            + " 项。\n"
            + "\n".join("批次 " + r["prompt_id"] for r in scope[:50])
            + "\n请在本机管理页处理或关闭相应提醒；关闭提醒不删除资料。"
        )
        if len(scope) > 50:
            body += "\n其余待办请在管理页查看。"
        db.execute(
            "INSERT INTO wx_batch_reminders VALUES(?,?,?,?,?,'sending')",
            (identity, binding.account_id, clock, json.dumps(scope), body),
        )

    def authorize() -> None:
        with inbox.connection(config, binding) as current:
            saved = current.execute(
                "SELECT state FROM wx_batch_reminders WHERE id=?", (identity,)
            ).fetchone()
            if saved is None or saved[0] != "sending" or due(current, binding, clock) != scope:
                raise InvalidInput("reminder_scope_changed")

    state = "unknown"
    try:
        async with asyncio.timeout(30):
            if await sender(binding.owner_id, body, authorize) is True:
                state = "sent"
    except asyncio.CancelledError:
        raise  # Persisted sending is unknown after restart, never eligible for replay.
    except Exception:
        pass  # Store no third-party text/credentials.
    with inbox.connection(config, binding) as db, database.transaction(db):
        db.execute(
            "UPDATE wx_batch_reminders SET state=? WHERE id=? AND state='sending'",
            (state, identity),
        )
    return {"state": state, "id": identity}
