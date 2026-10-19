"""Synchronize bound task metadata without executing, publishing or returning source content."""

import sqlite3
from typing import Any

from . import (
    answer_tasks,
    article_bindings,
    auth,
    database,
    execution_ledger,
    local_query_ledger,
    local_query_tasks,
)
from . import weixin_inbox as inbox
from .config import Config, InvalidInput
from .weixin_events import Binding
from .weixin_note_queries import identity as note_identity


def initialize_locked(journal: sqlite3.Connection) -> None:
    journal.execute(
        "CREATE TABLE IF NOT EXISTS wx_task_dispatches("
        "run_id TEXT PRIMARY KEY REFERENCES wx_task_bindings(run_id),"
        "phase TEXT NOT NULL CHECK(phase IN ('observed','claimed','finished','unknown')),"
        "run_state TEXT NOT NULL,batch_state TEXT NOT NULL) STRICT"
    )


def observe_locked(
    config: Config,
    journal: sqlite3.Connection,
    db: sqlite3.Connection,
    binding: Binding,
    run_id: str,
) -> dict[str, Any]:
    """Caller holds storage lock; main cancellation precedes the repeatable journal snapshot."""
    inbox.bound(journal, binding)
    if not journal.execute("SELECT 1 FROM sqlite_master WHERE name='wx_task_bindings'").fetchone():
        raise InvalidInput("no bound tasks")
    bound = journal.execute(
        "SELECT t.*,b.state AS batch_state FROM wx_task_bindings t "
        "JOIN wx_batches b ON b.id=t.batch_id AND b.account_id=t.account_id "
        "WHERE t.run_id=? AND t.account_id=?",
        (run_id, binding.account_id),
    ).fetchone()
    if bound is None:
        raise auth.Denied(404)
    run = db.execute("SELECT * FROM execution_runs WHERE id=?", (run_id,)).fetchone()
    local = db.execute("SELECT * FROM local_query_tasks WHERE id=?", (run_id,)).fetchone()
    if run is not None and local is not None:
        raise InvalidInput("query_execution_identity_conflict")
    if local is not None:
        run = local
    if run is not None:
        task = (
            local
            if local is not None
            else (
                answer_tasks.get(db, run_id)
                if bound["kind"] == "answer"
                else article_bindings.get(db, run_id)
            )
        )
        if (
            run["client_id"] != binding.client_id
            or run["vault_id"] != binding.vault_id
            or run["config_digest"] != bound["policy_digest"]
            or task is None
            or task["receipt_id"]
            != (
                None
                if bound["input_receipt_id"] == note_identity(bound["batch_id"])
                else bound["input_receipt_id"]
            )
        ):
            raise auth.Denied(404)
        if bound["batch_state"] == "cancelled" and run["state"] in {
            "queued",
            "running",
            "waiting_budget",
            "interrupted",
        }:
            if local is not None:
                local_query_ledger.stop(config, db, binding.client_id, run_id)
                run = local_query_tasks.get(db, binding.client_id, run_id)
            else:
                execution_ledger.transition(db, run_id, "cancelled", "weixin_batch_cancelled")
                run = execution_ledger.get_run(db, run_id)
    state = "missing" if run is None else run["state"]
    with database.transaction(journal):
        initialize_locked(journal)
        journal.execute(
            "INSERT INTO wx_task_dispatches VALUES(?,'observed',?,?) "
            "ON CONFLICT(run_id) DO UPDATE SET run_state=excluded.run_state,"
            "batch_state=excluded.batch_state",
            (run_id, state, bound["batch_state"]),
        )
        phase = journal.execute(
            "SELECT phase FROM wx_task_dispatches WHERE run_id=?", (run_id,)
        ).fetchone()[0]
    return {
        "run_id": run_id,
        "batch_id": bound["batch_id"],
        "kind": bound["kind"],
        "run_state": state,
        "batch_state": bound["batch_state"],
        "dispatch_phase": phase,
    }


def synchronize(config: Config, binding: Binding, run_id: str) -> dict[str, Any]:
    """No network or retry. Cancellation is cooperative; finished effects remain historical."""
    with inbox.connection(config, binding) as journal:
        db = database.connect(config.state_root)
        try:
            return observe_locked(config, journal, db, binding, run_id)
        finally:
            db.close()
