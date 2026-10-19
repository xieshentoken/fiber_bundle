"""Durable owner intent boundaries and frozen-batch plans, separate from receiver plumbing."""

import json
import sqlite3
from typing import Any

from . import database
from . import weixin_inbox as inbox
from .config import Config, IntegrityError
from .inputs import canonical
from .objects import digest
from .weixin_events import Binding
from .weixin_intent_text import classify
from .weixin_note_queries import PREFIXES


def initialize(config: Config, binding: Binding) -> None:
    with inbox.connection(config, binding) as db, database.transaction(db):
        inbox.bound(db, binding)
        db.execute(
            "CREATE TABLE IF NOT EXISTS wx_event_intents("
            "account_id TEXT NOT NULL,event_id TEXT NOT NULL,raw_sha256 TEXT NOT NULL,"
            "intent_json TEXT NOT NULL,intent_sha256 TEXT NOT NULL,"
            "PRIMARY KEY(account_id,event_id),"
            "FOREIGN KEY(account_id,event_id) REFERENCES events(account_id,event_id)) STRICT"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS wx_batch_intents("
            "batch_id TEXT PRIMARY KEY REFERENCES wx_batches(id),account_id TEXT NOT NULL,"
            "plan_json TEXT NOT NULL,plan_sha256 TEXT NOT NULL) STRICT"
        )


def capture_locked(db: sqlite3.Connection, event: sqlite3.Row, message: dict[str, Any]) -> None:
    value = canonical(classify(message))
    db.execute(
        "INSERT INTO wx_event_intents VALUES(?,?,?,?,?)",
        (
            event["account_id"],
            event["event_id"],
            event["raw_sha256"],
            value,
            digest(value.encode()),
        ),
    )


def event_locked(db: sqlite3.Connection, event: sqlite3.Row) -> dict[str, Any] | None:
    declared = (
        db.execute(
            "SELECT result_json FROM wx_command_receipts WHERE account_id=? AND event_id=?",
            (event["account_id"], event["event_id"]),
        ).fetchone()
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='wx_command_receipts'").fetchone()
        else None
    )
    required = declared is not None and json.loads(declared[0]).get("intent_version") in (1, 2)
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='wx_event_intents'").fetchone():
        if required:
            raise IntegrityError("ingress intent ledger missing")
        return None
    row = db.execute(
        "SELECT * FROM wx_event_intents WHERE account_id=? AND event_id=?",
        (event["account_id"], event["event_id"]),
    ).fetchone()
    if row is None:
        if required:
            raise IntegrityError("ingress intent missing")
        return None  # Legacy sources are never retrospectively interpreted.
    if (
        row["raw_sha256"] != event["raw_sha256"]
        or digest(bytes(event["raw"])) != event["raw_sha256"]
        or digest(row["intent_json"].encode()) != row["intent_sha256"]
    ):
        raise IntegrityError("ingress intent digest mismatch")
    intent = json.loads(row["intent_json"])
    if (
        declared is not None
        and json.loads(declared[0]).get("intent_version") != intent.get("version")
    ) or intent != classify(json.loads(event["raw"]), version=intent.get("version")):
        raise IntegrityError("ingress intent source changed")
    return dict(intent)


def preview_locked(
    db: sqlite3.Connection, binding: Binding, batch_id: str
) -> dict[str, Any] | None:
    """Validate readiness before selection, even if the frozen plan has not been persisted yet."""
    return _reduce(db, binding, batch_id)


def _reduce(db: sqlite3.Connection, binding: Binding, batch_id: str) -> dict[str, Any] | None:
    events = db.execute(
        "SELECT e.* FROM wx_batch_members m JOIN events e ON e.account_id=m.account_id "
        "AND e.event_id=m.event_id WHERE m.account_id=? AND m.batch_id=? ORDER BY m.ordinal",
        (binding.account_id, batch_id),
    ).fetchall()
    intents = [event_locked(db, e) for e in events]
    if not events or any(i is None for i in intents):
        return None
    verified = [i for i in intents if i is not None]
    questions = [i["question"] for i in verified if i["question"] is not None]
    sources = sum(bool(i["source_keys"]) for i in verified)
    reasons = [i["reason"] for i in verified if i["reason"] not in (None, "question_required")]
    if len(questions) > 1:
        reasons.append("multiple_questions")
    query_existing = bool(
        len(events) == 1
        and all(i["version"] == 2 for i in verified)
        and not sources
        and len(questions) == 1
        and questions[0].startswith(PREFIXES)
        and not any(i["also_save"] for i in verified)
    )
    if (
        any(
            i["version"] == 2 and i["question"] and i["question"].startswith(PREFIXES)
            for i in verified
        )
        and not query_existing
    ):
        reasons.append("conflicting_query_scope")
    if not sources and not query_existing:
        reasons.append("source_scope_required")
    also_save = any(i["also_save"] for i in verified)
    if also_save and any(
        i["consult"]
        and not i["also_save"]
        and i["question"]
        and ("不要保存" in i["question"] or "不入库" in i["question"])
        for i in verified
    ):
        reasons.append("conflicting_save_request")
    question = questions[0] if len(questions) == 1 else None
    consult = any(i["consult"] for i in verified)
    action = "save_one" if also_save or not consult else "consult"
    return {
        **({"query_existing": True} if query_existing else {}),
        "members": [[e["job_id"], e["raw_sha256"]] for e in events],
        "source_count": sources,
        "question": question,
        "also_save": also_save,
        "action": action,
        "reason": reasons[0] if reasons else None,
        "auto": len(events) == 1 and not reasons and not (consult and not question),
    }


def plan_locked(db: sqlite3.Connection, binding: Binding, batch_id: str) -> dict[str, Any] | None:
    plan = _reduce(db, binding, batch_id)
    if plan is None:
        return None
    value = canonical(plan)
    row = db.execute(
        "SELECT * FROM wx_batch_intents WHERE batch_id=? AND account_id=?",
        (batch_id, binding.account_id),
    ).fetchone()
    if row is not None:
        if row["plan_json"] != value or row["plan_sha256"] != digest(value.encode()):
            raise IntegrityError("batch intent scope changed")
    else:
        db.execute(
            "INSERT INTO wx_batch_intents VALUES(?,?,?,?)",
            (batch_id, binding.account_id, value, digest(value.encode())),
        )
    return plan


def read_locked(db: sqlite3.Connection, binding: Binding, batch_id: str) -> dict[str, Any] | None:
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='wx_batch_intents'").fetchone():
        if _reduce(db, binding, batch_id) is not None:
            raise IntegrityError("batch intent ledger missing")
        return None
    row = db.execute(
        "SELECT * FROM wx_batch_intents WHERE batch_id=? AND account_id=?",
        (batch_id, binding.account_id),
    ).fetchone()
    if row is None:
        batch = db.execute(
            "SELECT state FROM wx_batches WHERE id=? AND account_id=?",
            (batch_id, binding.account_id),
        ).fetchone()
        if batch and batch[0] == "selected" and _reduce(db, binding, batch_id) is not None:
            raise IntegrityError("selected batch intent missing")
        return None
    plan = _reduce(db, binding, batch_id)
    if (
        plan is None
        or canonical(plan) != row["plan_json"]
        or digest(row["plan_json"].encode()) != row["plan_sha256"]
    ):
        raise IntegrityError("batch intent scope changed")
    return plan


def query_after_save(plan: dict[str, Any] | None, action: str, question: str | None) -> bool:
    return bool(
        plan and plan["also_save"] and question and action in {"save_one", "save_separately"}
    )
