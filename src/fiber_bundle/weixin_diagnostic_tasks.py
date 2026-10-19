"""Metadata-only task diagnostics. Never authorize effects, reconcile or scan evidence."""

import sqlite3
from typing import Any

from . import auth, execution
from .config import IntegrityError
from .weixin_events import Binding


def exists(db: sqlite3.Connection, table: str) -> bool:
    return (
        db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        is not None
    )


def tasks(
    journal: sqlite3.Connection, main: sqlite3.Connection, binding: Binding, batch_id: str
) -> dict[str, Any]:
    if not exists(journal, "wx_task_bindings"):
        return {"count": 0, "items": [], "truncated": False}
    count = journal.execute(
        "SELECT count(*) FROM wx_task_bindings WHERE account_id=? AND batch_id=?",
        (binding.account_id, batch_id),
    ).fetchone()[0]
    rows = journal.execute(
        "SELECT run_id,kind,policy_digest FROM wx_task_bindings "
        "WHERE account_id=? AND batch_id=? ORDER BY rowid LIMIT 100",
        (binding.account_id, batch_id),
    ).fetchall()
    items = []
    for row in rows:
        rid = execution.identifier(row["run_id"])
        run = main.execute("SELECT * FROM execution_runs WHERE id=?", (rid,)).fetchone()
        if run is not None and (
            run["client_id"] != binding.client_id
            or run["vault_id"] != binding.vault_id
            or run["config_digest"] != row["policy_digest"]
        ):
            raise IntegrityError("diagnostic task scope mismatch")
        phase = None
        if exists(journal, "wx_task_dispatches"):
            saved = journal.execute(
                "SELECT phase FROM wx_task_dispatches WHERE run_id=?", (rid,)
            ).fetchone()
            phase = saved[0] if saved else None
        pub = None
        if exists(journal, "wx_task_publications"):
            saved = journal.execute(
                "SELECT phase FROM wx_task_publications WHERE run_id=? AND batch_id=?",
                (rid, batch_id),
            ).fetchone()
            pub = saved[0] if saved else None
        fees = dict(
            main.execute(
                "SELECT count(*) AS calls,coalesce(sum(coalesce(charged_nusd,reserved_nusd)),0) "
                "AS accounted_nusd,coalesce(sum(charged_nusd),0) AS charged_nusd,"
                "coalesce(sum(CASE WHEN state!='settled' THEN reserved_nusd ELSE 0 END),0) "
                "AS unsettled_reserved_nusd FROM model_calls WHERE run_id=?",
                (rid,),
            ).fetchone()
        )
        actions = []
        if row["kind"] == "answer":
            try:
                auth.grant(main, binding.client_id, binding.vault_id, "query")
            except auth.Denied:
                actions.append("review_current_query_permission_do_not_execute_or_deliver")
        state = run["state"] if run else "missing"
        if state == "missing":
            actions.append("repeat_original_submit_same_policy")
        if phase in {"claimed", "unknown"}:
            actions.append("verify_executor_stopped_then_worker_reconcile")
        if state == "waiting_budget":
            actions.append("review_budget_keep_existing_run_and_fees")
        if state == "interrupted":
            actions.append("verify_executor_and_fees_before_explicit_recovery")
        if fees["unsettled_reserved_nusd"]:
            actions.append("verify_provider_usage_keep_reservation")
        if pub in {"claimed", "failed"}:
            actions.append("verify_files_then_publication_reconcile")
        elif row["kind"] == "article" and state == "succeeded" and pub is None:
            actions.append("capture_is_not_publication_plan_before_result_freeze")
        items.append(
            {
                "run_id": rid,
                "kind": row["kind"],
                "state": state,
                "dispatch_phase": phase,
                "publication_phase": pub,
                "fees": fees,
                "actions": actions,
            }
        )
    return {"count": count, "items": items, "truncated": count > len(items)}
