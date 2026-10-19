"""Opt-in bounded queue submission; reuse durable bindings without executing any task."""

import fcntl
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from . import answer_tasks, article_bindings, database, execution_ledger, weixin_note_queries
from . import weixin_batches as batches
from . import weixin_inbox as inbox
from . import weixin_store as handoff
from . import weixin_task_bindings as bindings
from . import weixin_task_inputs as inputs
from .article_worker import CapturePolicy
from .config import Config, InvalidInput, contained
from .execution_config import ExecutionConfig
from .inputs import canonical
from .weixin_events import Binding
from .weixin_intent_text import retrieval_query


def limit(value: int) -> int:
    if type(value) is not int or not 0 <= value <= 100:
        raise InvalidInput("submission limit must be an integer from 0 to 100")
    return value


@contextmanager
def worker_lock(config: Config) -> Iterator[None]:
    config.validate()
    fd = os.open(
        contained(config.state_root, "weixin-submission-worker.lock"),
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InvalidInput("Weixin submission worker already active") from None
        yield
    finally:
        os.close(fd)


def _complete(
    config: Config,
    journal: sqlite3.Connection,
    db: sqlite3.Connection,
    binding: Binding,
    target: dict[str, Any],
    view: dict[str, Any],
    policy: ExecutionConfig,
    capture: CapturePolicy | None,
) -> bool:
    if not journal.execute("SELECT 1 FROM sqlite_master WHERE name='wx_task_bindings'").fetchone():
        return False
    identity = f"weixin-task:{target['batch_id']}:{target['parent_receipt_id']}:{target['kind']}"
    rows = journal.execute(
        "SELECT * FROM wx_task_bindings WHERE batch_id=? AND account_id=?",
        (target["batch_id"], binding.account_id),
    ).fetchall()
    for bound in rows:
        note_query = weixin_note_queries.existing(view)
        receipt = db.execute(
            "SELECT event_id FROM receipts WHERE job_id=?", (bound["input_receipt_id"],)
        ).fetchone()
        if not note_query and receipt is None:
            raise InvalidInput("submission input missing")
        event_id = bound["input_receipt_id"] if note_query else receipt["event_id"]
        if event_id != identity:
            continue
        request = (
            {
                "receipt_id": bound["input_receipt_id"],
                "request": answer_tasks.request_data(
                    answer_tasks.NoteAnswerInput(
                        binding.vault_id,
                        weixin_note_queries.question(view["question"]),
                        retrieval_query(weixin_note_queries.question(view["question"])),
                        submission_id=identity,
                    )
                    if note_query
                    else answer_tasks.AnswerInput(
                        bound["input_receipt_id"],
                        "weixin-task:" + target["batch_id"],
                        view["question"],
                        view.get("retrieval_query", view["question"]),
                        submission_id=identity,
                    )
                ),
            }
            if target["kind"] == "answer"
            else {
                "receipt_id": bound["input_receipt_id"],
                "image": capture.image,
                "timeout": capture.timeout_seconds,
            }
            if capture is not None
            else None
        )
        if (
            bound["kind"] != target["kind"]
            or bound["policy_digest"] != policy.digest
            or bound["request_json"] != canonical(request)
        ):
            raise InvalidInput("task submission conflicts with saved binding")
        run = db.execute("SELECT * FROM execution_runs WHERE id=?", (bound["run_id"],)).fetchone()
        if run is None:
            return False  # submit restores the same journal run ID, never allocates a replacement.
        if run["client_id"] != binding.client_id or run["vault_id"] != binding.vault_id:
            raise InvalidInput("execution identity conflicts with binding")
        task = (
            answer_tasks.get(db, run["id"])
            if target["kind"] == "answer"
            else article_bindings.get(db, run["id"])
        )
        if task is None or task["receipt_id"] != (
            None if note_query else bound["input_receipt_id"]
        ):
            raise InvalidInput("execution task conflicts with binding")
        execution_ledger.authorize(config, db, run)
        return True  # Any existing run state is complete submission; never restart it.
    return False


def _next(
    config: Config,
    binding: Binding,
    policy: ExecutionConfig,
    capture: CapturePolicy | None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    pending: list[dict[str, Any]] = []
    with inbox.connection(config, binding) as journal:
        inbox.bound(journal, binding)
        if not journal.execute(
            "SELECT 1 FROM sqlite_master WHERE name='wx_store_handoffs'"
        ).fetchone():
            return None, pending
        rows = journal.execute(
            "SELECT b.id FROM wx_batches b JOIN wx_store_handoffs h ON h.batch_id=b.id "
            "AND h.account_id=b.account_id WHERE b.account_id=? "
            "AND b.state='selected' AND h.state='stored' ORDER BY b.rowid",
            (binding.account_id,),
        ).fetchall()
        db = database.connect(config.state_root)
        try:
            for row in rows:
                batch = batches.snapshot(journal, binding, row["id"])
                if not batch["content_ready"] or batch["needs_question"]:
                    continue
                view = handoff.read_locked(journal, binding, row["id"])
                if view["action"] != batch["action"]:
                    raise InvalidInput("handoff action changed")
                for target in inputs.targets_locked(config, db, binding, view):
                    if target["kind"] == "article" and capture is None:
                        target.setdefault("reason", "capture_policy_required")
                    if "reason" in target:
                        if len(pending) < 100:
                            pending.append(target)
                        continue
                    if not _complete(config, journal, db, binding, target, view, policy, capture):
                        return target, pending
        finally:
            db.close()
    return None, pending


def dispatch(
    config: Config,
    binding: Binding,
    policy: ExecutionConfig,
    *,
    maximum: int = 0,
    capture_policy: CapturePolicy | None = None,
) -> dict[str, Any]:
    """Count queued/restored runs, not batches. Failure stops and preserves the fixed intent."""
    if limit(maximum) == 0:
        return {"state": "disabled", "processed": 0, "results": [], "pending": []}
    policy.validate()
    if capture_policy is not None:
        capture_policy.validate()
    results: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    with worker_lock(config):
        for _ in range(maximum):
            target, pending = _next(config, binding, policy, capture_policy)
            if target is None:
                return {
                    "state": "pending" if pending else "idle",
                    "processed": len(results),
                    "results": results,
                    "pending": pending,
                }
            result = bindings.submit(
                config,
                binding,
                target["batch_id"],
                policy,
                capture_policy=capture_policy if target["kind"] == "article" else None,
                parent_receipt_id=target["parent_receipt_id"]
                if target["kind"] == "article"
                else None,
            )
            results.append({key: result[key] for key in ("run_id", "state", "kind", "batch_id")})
    return {
        "state": "limit_reached",
        "processed": len(results),
        "results": results,
        "pending": pending,
    }
