"""Bounded read-only operational snapshot without payloads, secrets or automatic recovery."""

import re
import sqlite3
from typing import Any

from . import database, execution
from . import weixin_batches as batches
from . import weixin_inbox as inbox
from . import weixin_store as store
from .config import Config, IntegrityError, InvalidInput
from .weixin_diagnostic_tasks import exists, tasks
from .weixin_events import Binding


def outbound(db: sqlite3.Connection, binding: Binding) -> list[dict[str, Any]]:
    result = []
    for table, key in (
        ("wx_confirmation_prompts", "id"),
        ("wx_receipt_deliveries", "job_id"),
        ("wx_task_deliveries", "batch_id"),
    ):
        if not exists(db, table):
            continue
        count = db.execute(
            f"SELECT count(*) FROM {table} WHERE account_id=? AND state IN ('sending','unknown')",
            (binding.account_id,),
        ).fetchone()[0]
        if not count:
            continue
        rows = db.execute(
            f"SELECT {key},state FROM {table} WHERE account_id=? "
            "AND state IN ('sending','unknown') ORDER BY rowid LIMIT 100",
            (binding.account_id,),
        ).fetchall()
        items = []
        for row in rows:
            identifier = row[key]
            if key == "id":
                if not re.fullmatch(r"[0-9a-f]{16}", identifier):
                    raise IntegrityError("invalid diagnostic prompt id")
            else:
                identifier = execution.identifier(identifier)
            items.append({"id": identifier, "state": row["state"]})
        result.append(
            {
                "ledger": table,
                "count": count,
                "items": items,
                "truncated": count > len(items),
                "action": "manual_channel_check_keep_outbound_disabled",
            }
        )
    return result


def batch(
    db: sqlite3.Connection, main: sqlite3.Connection, binding: Binding, bid: str
) -> dict[str, Any]:
    bid = execution.identifier(bid)
    view = batches.snapshot(db, binding, bid)
    actions = []
    states = [member["state"] for member in view["members"]]
    if "unsupported" in states:
        actions.append("supply_supported_original_in_new_batch")
    elif "incomplete" in states:
        actions.append("inspect_media_attempts_before_explicit_download")
    if view["needs_question"] or view["state"] == "frozen":
        actions.append("owner_clarification_or_exact_batch_choice")
    handoff = None
    expected_tasks = None
    if exists(db, "wx_store_handoffs"):
        saved = db.execute(
            "SELECT state FROM wx_store_handoffs WHERE account_id=? AND batch_id=?",
            (binding.account_id, bid),
        ).fetchone()
        handoff = saved[0] if saved else None
        if saved:
            stored = store.read_locked(db, binding, bid)
            expected_tasks = (
                1
                if stored["action"] == "consult" or stored.get("query_after_save")
                else len(stored["receipts"])
            )
        if handoff == "prepared":
            actions.append("inspect_store_then_explicit_resume")
    store_attempt = None
    if exists(db, "wx_store_attempts"):
        saved = db.execute(
            "SELECT id,state FROM wx_store_attempts WHERE account_id=? AND batch_id=? "
            "ORDER BY id DESC LIMIT 1",
            (binding.account_id, bid),
        ).fetchone()
        if saved:
            store_attempt = dict(saved)
            if saved["state"] in {"running", "failed"}:
                actions.append("verify_store_worker_stopped_then_explicit_resume")
    media = []
    if exists(db, "wx_download_attempts"):
        for row in db.execute(
            "SELECT a.id,a.job_id,a.attempt,a.state FROM wx_download_attempts a "
            "JOIN events e ON e.job_id=a.job_id AND e.account_id=a.account_id "
            "JOIN wx_batch_members m ON m.event_id=e.event_id AND m.account_id=e.account_id "
            "WHERE a.account_id=? AND m.batch_id=? ORDER BY a.rowid DESC LIMIT 101",
            (binding.account_id, bid),
        ):
            item = dict(row)
            item["id"] = execution.identifier(item["id"])
            item["job_id"] = execution.identifier(item["job_id"])
            item["action"] = {
                "running": "verify_media_worker_stopped_then_reconcile",
                "failed": "inspect_failure_then_explicit_retry",
                "interrupted": "reconcile_then_review_explicit_retry",
                "queued": "explicit_bounded_media_dispatch",
                "succeeded": "none",
            }[item["state"]]
            media.append(item)
    bound_tasks = tasks(db, main, binding, bid)
    if handoff == "stored" and expected_tasks is not None and bound_tasks["count"] < expected_tasks:
        actions.append("review_fixed_submission_scope_and_policy")
    if view["state"] == "cancelled":
        actions = ["keep_history_do_not_resume_cancelled_batch"]
        for item in media[:100]:
            item["action"] = "keep_history_do_not_resume_cancelled_batch"
        for task in bound_tasks["items"]:
            task["actions"] = ["keep_history_verify_inflight_effects_without_replay"]
    return {
        "batch_id": bid,
        "state": view["state"],
        "action": view["action"],
        "member_count": len(states),
        "incomplete_count": states.count("incomplete"),
        "unsupported_count": states.count("unsupported"),
        "needs_question": view["needs_question"],
        "store_state": handoff,
        "store_attempt": store_attempt,
        "expected_tasks": expected_tasks,
        "tasks": bound_tasks,
        "media_attempts": media[:100],
        "media_truncated": len(media) > 100,
        "actions": actions,
    }


def inspect(
    config: Config,
    binding: Binding,
    *,
    batch_id: str | None = None,
    after: int = 0,
    limit: int = 20,
) -> dict[str, Any]:
    """Current add authorization required. No evidence scan or freshness/success certification.

    Account-wide send blockers are included even when outside the requested batch page.
    Existing connections share the common storage lock; query_only prevents all SQL writes.
    """
    if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise InvalidInput("invalid diagnostic page")
    if batch_id is not None:
        batch_id = execution.identifier(batch_id)
        if after:
            raise InvalidInput("batch filter cannot use cursor")
    with inbox.connection(config, binding) as db:
        db.execute("PRAGMA query_only=ON")
        inbox.bound(db, binding)
        main = database.connect(config.state_root)
        try:
            main.execute("PRAGMA query_only=ON")
            rows = []
            if exists(db, "wx_batches"):
                rows = db.execute(
                    "SELECT rowid,id FROM wx_batches WHERE account_id=? AND rowid>? "
                    "AND (? IS NULL OR id=?) ORDER BY rowid LIMIT ?",
                    (binding.account_id, after, batch_id, batch_id, limit + 1),
                ).fetchall()
            if batch_id is not None and not rows:
                raise InvalidInput("unknown diagnostic batch")
            blockers = outbound(db, binding)
            return {
                "state": "blocked" if blockers else "inspected",
                "batches": [batch(db, main, binding, row["id"]) for row in rows[:limit]],
                "next_cursor": rows[limit - 1]["rowid"] if len(rows) > limit else None,
                "outbound_blockers": blockers,
                "evidence_checked": False,
            }
        finally:
            main.close()
