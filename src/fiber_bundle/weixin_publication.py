"""Explicit batch publication intent; reuse M4 without replaying uncertain effects."""

import json
import sqlite3
from typing import Any

from . import article_publication, database, execution_ledger
from . import weixin_inbox as inbox
from . import weixin_store as handoff
from . import weixin_task_inputs as inputs
from .config import Config, IntegrityError, InvalidInput
from .inputs import canonical
from .objects import digest
from .weixin_events import Binding


def planned_locked(journal: sqlite3.Connection, binding: Binding, batch_id: str) -> list[str]:
    inbox.bound(journal, binding)
    if not journal.execute(
        "SELECT 1 FROM sqlite_master WHERE name='wx_publication_batches'"
    ).fetchone():
        return []
    plan = journal.execute(
        "SELECT * FROM wx_publication_batches WHERE batch_id=? AND account_id=?",
        (batch_id, binding.account_id),
    ).fetchone()
    if plan is None:
        return []
    if digest(plan["runs_json"].encode()) != plan["runs_sha256"]:
        raise IntegrityError("publication scope changed")
    ids = json.loads(plan["runs_json"])
    rows = journal.execute(
        "SELECT run_id FROM wx_task_publications WHERE batch_id=? ORDER BY run_id", (batch_id,)
    ).fetchall()
    if not ids or ids != sorted(r[0] for r in rows):
        raise IntegrityError("publication scope incomplete")
    return list(ids)


def plan(config: Config, binding: Binding, batch_id: str) -> dict[str, Any]:
    """Opt in before preparing a result receipt; freeze all already-submitted article runs."""
    with inbox.connection(config, binding) as journal:
        view = handoff.read_locked(journal, binding, batch_id)
        if (
            view["batch_state"] != "selected"
            or view["store_state"] != "stored"
            or view["retention"] != "knowledge"
            or view.get("query_after_save")
        ):
            raise InvalidInput("publication requires an active saved article batch")
        if (
            journal.execute(
                "SELECT 1 FROM sqlite_master WHERE name='wx_task_deliveries'"
            ).fetchone()
            and journal.execute(
                "SELECT 1 FROM wx_task_deliveries WHERE batch_id=?", (batch_id,)
            ).fetchone()
        ):
            raise InvalidInput("result receipt already frozen")
        db = database.connect(config.state_root)
        try:
            targets = inputs.targets_locked(config, db, binding, view)
            if not targets or any(t["kind"] != "article" or "reason" in t for t in targets):
                raise InvalidInput("publication article scope unsupported")
            rows = journal.execute(
                "SELECT * FROM wx_task_bindings WHERE batch_id=? AND account_id=?",
                (batch_id, binding.account_id),
            ).fetchall()
            expected = {f"weixin-task:{batch_id}:{t['parent_receipt_id']}:article" for t in targets}
            actual = set()
            for row in rows:
                run = execution_ledger.get_run(db, row["run_id"])
                execution_ledger.authorize(config, db, run)
                receipt = db.execute(
                    "SELECT event_id FROM receipts WHERE job_id=?", (row["input_receipt_id"],)
                ).fetchone()
                if row["kind"] != "article" or receipt is None:
                    raise InvalidInput("publication task scope changed")
                actual.add(receipt[0])
            if len(rows) != len(expected) or actual != expected:
                raise InvalidInput("publication submissions incomplete")
            ids = sorted(r["run_id"] for r in rows)
            encoded = canonical(ids)
            with database.transaction(journal):
                journal.execute(
                    "CREATE TABLE IF NOT EXISTS wx_publication_batches("
                    "batch_id TEXT PRIMARY KEY REFERENCES wx_batches(id),account_id TEXT NOT NULL,"
                    "runs_json TEXT NOT NULL,runs_sha256 TEXT NOT NULL) STRICT"
                )
                journal.execute(
                    "CREATE TABLE IF NOT EXISTS wx_task_publications("
                    "run_id TEXT PRIMARY KEY REFERENCES wx_task_bindings(run_id),"
                    "batch_id TEXT NOT NULL REFERENCES wx_publication_batches(batch_id),"
                    "phase TEXT NOT NULL "
                    "CHECK(phase IN ('pending','claimed','published','failed')),"
                    "result_json TEXT,result_sha256 TEXT) STRICT"
                )
                previous = planned_locked(journal, binding, batch_id)
                if previous and previous != ids:
                    raise IntegrityError("publication scope changed")
                if not previous:
                    journal.execute(
                        "INSERT INTO wx_publication_batches VALUES(?,?,?,?)",
                        (batch_id, binding.account_id, encoded, digest(encoded.encode())),
                    )
                    journal.executemany(
                        "INSERT INTO wx_task_publications VALUES(?,?,'pending',NULL,NULL)",
                        [(rid, batch_id) for rid in ids],
                    )
            return {"batch_id": batch_id, "run_ids": ids, "published": False}
        finally:
            db.close()


def publish_locked(
    config: Config,
    journal: sqlite3.Connection,
    db: sqlite3.Connection,
    binding: Binding,
    run_id: str,
) -> dict[str, Any]:
    """Caller holds storage lock. The committed claim survives exceptions and process death."""
    row = journal.execute("SELECT * FROM wx_task_publications WHERE run_id=?", (run_id,)).fetchone()
    if row is None or run_id not in planned_locked(journal, binding, row["batch_id"]):
        raise InvalidInput("no publication intent")
    article_publication.bound(config, db, binding.client_id, run_id)
    with database.transaction(journal):
        journal.execute("UPDATE wx_task_publications SET phase='claimed' WHERE run_id=?", (run_id,))
    try:
        result = article_publication.publish_locked(config, db, binding.client_id, run_id)
    except Exception:
        with database.transaction(journal):
            journal.execute(
                "UPDATE wx_task_publications SET phase='failed' WHERE run_id=?", (run_id,)
            )
        raise
    encoded = canonical(result)
    with database.transaction(journal):
        journal.execute(
            "UPDATE wx_task_publications SET phase='published',result_json=?,result_sha256=? "
            "WHERE run_id=?",
            (encoded, digest(encoded.encode()), run_id),
        )
    return {"run_id": run_id, **result}


def reconcile(config: Config, binding: Binding, run_id: str) -> dict[str, Any]:
    """Explicit M4 filesystem/ledger reconciliation; no recapture, model call or send."""
    with inbox.connection(config, binding) as journal:
        db = database.connect(config.state_root)
        try:
            return publish_locked(config, journal, db, binding, run_id)
        finally:
            db.close()
