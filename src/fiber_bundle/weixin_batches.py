"""Durable pre-dispatch batch boundaries and explicit user choices.

Local trusted API: channel commands must be authenticated before calling choose.
No source-text intent guessing, model invocation, publication or message sending.
"""

import math
import sqlite3
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from . import database, weixin_inbox, weixin_recovery
from . import weixin_intents as intents
from .config import Config, InvalidInput
from .inputs import text
from .weixin_events import Binding

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS wx_batches("
    "id TEXT PRIMARY KEY,account_id TEXT NOT NULL REFERENCES bindings(account_id),"
    "state TEXT NOT NULL CHECK(state IN ('buffering','frozen','selected','cancelled')),"
    "quiet_seconds REAL NOT NULL,last_received REAL NOT NULL,action TEXT,question TEXT) STRICT",
    "CREATE TABLE IF NOT EXISTS wx_batch_members("
    "batch_id TEXT NOT NULL REFERENCES wx_batches(id),account_id TEXT NOT NULL,"
    "event_id TEXT NOT NULL,ordinal INTEGER NOT NULL,"
    "PRIMARY KEY(account_id,event_id),UNIQUE(batch_id,ordinal),"
    "FOREIGN KEY(account_id,event_id) REFERENCES events(account_id,event_id)) STRICT",
    "CREATE UNIQUE INDEX IF NOT EXISTS wx_one_buffer ON wx_batches(account_id) "
    "WHERE state='buffering'",
)
ACTIONS = frozenset({"save_one", "save_separately", "consult", "cancel"})


def initialize(config: Config, binding: Binding) -> None:
    """Explicit additive batch tables; existing journal schema/data stay unchanged."""
    with weixin_inbox.connection(config, binding) as db, database.transaction(db):
        weixin_inbox.bound(db, binding)
        for statement in SCHEMA:
            db.execute(statement)


def seconds(value: float) -> float:
    if type(value) not in (float, int) or not math.isfinite(value) or not 0 < value <= 300:
        raise InvalidInput("quiet window must be greater than zero and at most 300 seconds")
    return float(value)


def timestamp(now: datetime | None) -> float:
    value = datetime.now(UTC) if now is None else now
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise InvalidInput("batch clock must be timezone aware")
    return value.timestamp()


def snapshot(db: sqlite3.Connection, binding: Binding, batch_id: str) -> dict[str, Any]:
    batch = db.execute(
        "SELECT * FROM wx_batches WHERE id=? AND account_id=?", (batch_id, binding.account_id)
    ).fetchone()
    if batch is None:
        raise InvalidInput("unknown weixin batch")
    members = [
        {
            "job_id": row["job_id"],
            "state": weixin_recovery.current_state(db, binding, row["job_id"], row["state"]),
        }
        for row in db.execute(
            "SELECT e.job_id,e.state FROM wx_batch_members m JOIN events e "
            "ON e.account_id=m.account_id AND e.event_id=m.event_id "
            "WHERE m.batch_id=? ORDER BY m.ordinal",
            (batch_id,),
        )
    ]
    intent = intents.read_locked(db, binding, batch_id) or intents.preview_locked(
        db, binding, batch_id
    )
    return {
        "batch_id": batch_id,
        "state": batch["state"],
        "members": members,
        "action": batch["action"],
        "needs_question": batch["action"] == "consult" and batch["question"] is None,
        "content_ready": bool(members)
        and all(m["state"] == "pending" for m in members)
        and (
            intent is None or bool(intent["source_count"]) or intent.get("query_existing") is True
        ),
        "dispatched": False,
    }


def get(config: Config, binding: Binding, batch_id: str) -> dict[str, Any]:
    with weixin_inbox.connection(config, binding) as db:
        weixin_inbox.bound(db, binding)
        return snapshot(db, binding, batch_id)


def list_batches(
    config: Config, binding: Binding, *, after: int = 0, limit: int = 100
) -> dict[str, Any]:
    """Recover durable choices/pending scopes after restart; cursor is local row order."""
    if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise InvalidInput("invalid batch page")
    with weixin_inbox.connection(config, binding) as db:
        weixin_inbox.bound(db, binding)
        rows = db.execute(
            "SELECT rowid,id FROM wx_batches WHERE account_id=? AND rowid>? ORDER BY rowid LIMIT ?",
            (binding.account_id, after, limit + 1),
        ).fetchall()
        page = rows[:limit]
        return {
            "batches": [snapshot(db, binding, row["id"]) for row in page],
            "next_cursor": page[-1]["rowid"] if len(rows) > limit else None,
        }


def advance(
    config: Config,
    binding: Binding,
    *,
    quiet_seconds: float = 3,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Assign captured events once, then freeze expired batches before exposing prompts.

    Call after capture and on a timer. Time is a batching heuristic, not provenance.
    Each new batch fixes its window; changing configuration affects only new batches.
    Return touched/current buffer snapshots, not all historical pending batches.
    """
    quiet = seconds(quiet_seconds)
    current = timestamp(now)
    with weixin_inbox.connection(config, binding) as db, database.transaction(db):
        weixin_inbox.bound(db, binding)
        buffer = db.execute(
            "SELECT * FROM wx_batches WHERE account_id=? AND state='buffering'",
            (binding.account_id,),
        ).fetchone()
        touched: dict[str, None] = {}
        classification = ""
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='wx_command_receipts'").fetchone():
            # A bounded classification page must not freeze only part of an existing burst.
            if db.execute(
                "SELECT 1 FROM events e WHERE e.account_id=? AND NOT EXISTS "
                "(SELECT 1 FROM wx_command_receipts c WHERE c.account_id=e.account_id "
                "AND c.event_id=e.event_id) LIMIT 1",
                (binding.account_id,),
            ).fetchone():
                return []
            classification = (
                " AND EXISTS (SELECT 1 FROM wx_command_receipts c WHERE "
                "c.account_id=e.account_id AND c.event_id=e.event_id AND c.kind='source')"
            )
        events = db.execute(
            "SELECT e.* FROM events e WHERE e.account_id=? AND NOT EXISTS "
            "(SELECT 1 FROM wx_batch_members m WHERE m.account_id=e.account_id "
            "AND m.event_id=e.event_id)" + classification + " ORDER BY e.received_at,e.rowid",
            (binding.account_id,),
        ).fetchall()
        for event in events:
            received = datetime.fromisoformat(event["received_at"]).timestamp()
            if received > current:
                # Clock rollback: don't assign future events or force their timeout.
                continue
            if buffer is not None and received >= buffer["last_received"] + buffer["quiet_seconds"]:
                db.execute("UPDATE wx_batches SET state='frozen' WHERE id=?", (buffer["id"],))
                touched[buffer["id"]] = None
                buffer = None
            if buffer is None:
                batch_id = str(uuid4())
                db.execute(
                    "INSERT INTO wx_batches VALUES(?,?,'buffering',?,?,NULL,NULL)",
                    (batch_id, binding.account_id, quiet, received),
                )
            else:
                batch_id = buffer["id"]
                received = max(received, buffer["last_received"])
                db.execute("UPDATE wx_batches SET last_received=? WHERE id=?", (received, batch_id))
            ordinal = db.execute(
                "SELECT COUNT(*) FROM wx_batch_members WHERE batch_id=?", (batch_id,)
            ).fetchone()[0]
            db.execute(
                "INSERT INTO wx_batch_members VALUES(?,?,?,?)",
                (batch_id, binding.account_id, event["event_id"], ordinal),
            )
            touched[batch_id] = None
            buffer = db.execute("SELECT * FROM wx_batches WHERE id=?", (batch_id,)).fetchone()
        if buffer is not None:
            touched[buffer["id"]] = None
            if current >= buffer["last_received"] + buffer["quiet_seconds"]:
                db.execute("UPDATE wx_batches SET state='frozen' WHERE id=?", (buffer["id"],))
        return [snapshot(db, binding, batch_id) for batch_id in touched]


def choose(
    config: Config,
    binding: Binding,
    batch_id: str,
    action: str,
    *,
    question: str | None = None,
) -> dict[str, Any]:
    """Record a scope-bound explicit choice. Selection never means published/delivered.

    Exact retries are idempotent; cancellation remains available before dispatch.
    Other conflicting choices are rejected. A question may be
    supplied once after an unanswered consultation choice.
    """
    with weixin_inbox.connection(config, binding) as db, database.transaction(db):
        return choose_in_transaction(db, binding, batch_id, action, question=question)


def choose_in_transaction(
    db: sqlite3.Connection,
    binding: Binding,
    batch_id: str,
    action: str,
    *,
    question: str | None = None,
) -> dict[str, Any]:
    """Apply a choice in the caller's already protected journal transaction."""
    weixin_inbox.bound(db, binding)
    if not isinstance(action, str) or action not in ACTIONS:
        raise InvalidInput("unknown batch action")
    if question is not None:
        question = text(question, limit=4000)
        if action != "consult":
            raise InvalidInput("question requires consultation")
    view = snapshot(db, binding, batch_id)
    row = db.execute("SELECT * FROM wx_batches WHERE id=?", (batch_id,)).fetchone()
    intent = intents.read_locked(db, binding, batch_id)
    if intent is None and intents.preview_locked(db, binding, batch_id) is not None:
        intent = intents.plan_locked(db, binding, batch_id)
    if intent is not None and question is not None:
        question = text(question, limit=2000)
    if question is None and intent is not None and intent["reason"] is None:
        if action == "consult" or (
            action in {"save_one", "save_separately"} and intent["also_save"]
        ):
            question = intent["question"]
    if action == "cancel" and view["state"] in {"selected", "cancelled"}:
        # Preserve any consultation question along with all captured originals.
        db.execute(
            "UPDATE wx_batches SET state='cancelled',action='cancel' WHERE id=?", (batch_id,)
        )
        return snapshot(db, binding, batch_id)
    if view["state"] in {"selected", "cancelled"}:
        if row["action"] != action:
            raise InvalidInput("batch already has another choice")
        if row["question"] == question:
            return view
        if not (action == "consult" and row["question"] is None and question is not None):
            raise InvalidInput("batch question already fixed")
    elif view["state"] != "frozen":
        raise InvalidInput("batch must be frozen before choosing")
    if intent and intent.get("query_existing") and action not in {"consult", "cancel"}:
        raise InvalidInput("bound-Vault query cannot save question as material")
    if action != "cancel" and not view["content_ready"]:
        raise InvalidInput("batch content incomplete or unsupported")
    state = "cancelled" if action == "cancel" else "selected"
    db.execute(
        "UPDATE wx_batches SET state=?,action=?,question=? WHERE id=?",
        (state, action, question, batch_id),
    )
    return snapshot(db, binding, batch_id)
