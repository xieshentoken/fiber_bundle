"""Explicit bounded dispatch of already-bound Weixin tasks, with durable no-replay claims."""

import fcntl
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from . import answer_tasks, auth, database, execution, execution_ledger
from . import weixin_inbox as inbox
from . import weixin_task_status as status
from .config import Config, InvalidInput, StorageError, contained
from .execution_config import ExecutionConfig
from .weixin_events import Binding


@contextmanager
def worker_lock(config: Config) -> Iterator[None]:
    config.validate()
    fd = os.open(
        contained(config.state_root, "weixin-task-worker.lock"),
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InvalidInput("Weixin task worker already active") from None
        yield
    finally:
        os.close(fd)


def _claim(
    config: Config,
    binding: Binding,
    policy: ExecutionConfig,
    reserve: Callable[[sqlite3.Connection, str], bool] | None = None,
) -> dict[str, Any] | None:
    with inbox.connection(config, binding) as journal:
        inbox.bound(journal, binding)
        if not journal.execute(
            "SELECT 1 FROM sqlite_master WHERE name='wx_task_bindings'"
        ).fetchone():
            return None
        rows = journal.execute(
            "SELECT run_id FROM wx_task_bindings WHERE account_id=? AND policy_digest=? "
            "ORDER BY rowid",
            (binding.account_id, policy.digest),
        ).fetchall()
        db = database.connect(config.state_root)
        try:
            for row in rows:
                view = status.observe_locked(config, journal, db, binding, row["run_id"])
                if view["dispatch_phase"] == "finished":
                    continue
                if view["dispatch_phase"] in {"claimed", "unknown"}:
                    return {**view, "error_code": "task_dispatch_unresolved"}
                if view["run_state"] == "missing":
                    return {**view, "error_code": "task_submission_incomplete"}
                if view["run_state"] != "queued":
                    continue
                run = execution_ledger.get_run(db, row["run_id"])
                if db.execute("SELECT 1 FROM model_calls WHERE run_id=?", (run["id"],)).fetchone():
                    return {**view, "error_code": "task_prior_model_effects"}
                try:
                    execution_ledger.authorize(config, db, run)
                    answer = answer_tasks.get(db, run["id"])
                    if answer is not None:
                        answer_tasks.current(config, db, run, answer)
                except (auth.Denied, StorageError):
                    return {**view, "error_code": "task_preflight_rejected"}
                # Commit the claim before releasing storage lock or entering the executor.
                with database.transaction(journal):
                    if reserve is not None and not reserve(journal, run["id"]):
                        return {**view, "error_code": "model_quota_exhausted"}
                    journal.execute(
                        "UPDATE wx_task_dispatches SET phase='claimed' WHERE run_id=?", (run["id"],)
                    )
                return {**view, "dispatch_phase": "claimed"}
            return None
        finally:
            db.close()


def _settle(config: Config, binding: Binding, run_id: str, *, uncertain: bool) -> dict[str, Any]:
    with inbox.connection(config, binding) as journal:
        db = database.connect(config.state_root)
        try:
            view = status.observe_locked(config, journal, db, binding, run_id)
            phase = (
                "unknown"
                if uncertain or view["run_state"] in {"queued", "running", "missing"}
                else "finished"
            )
            with database.transaction(journal):
                journal.execute(
                    "UPDATE wx_task_dispatches SET phase=? WHERE run_id=?", (phase, run_id)
                )
            return {**view, "dispatch_phase": phase}
        finally:
            db.close()


def dispatch(
    config: Config,
    binding: Binding,
    policy: ExecutionConfig,
    *,
    maximum: int = 0,
    reserve: Callable[[sqlite3.Connection, str], bool] | None = None,
) -> dict[str, Any]:
    """Default off. Count execution attempts, stop on failure, never submit or retry a run."""
    if type(maximum) is not int or not 0 <= maximum <= 100:
        raise InvalidInput("task limit must be an integer from 0 to 100")
    if maximum == 0:
        return {"state": "disabled", "processed": 0, "results": []}
    policy.validate()
    results: list[dict[str, Any]] = []
    with worker_lock(config):
        for _ in range(maximum):
            view = _claim(config, binding, policy, reserve)
            if view is None:
                return {"state": "idle", "processed": len(results), "results": results}
            if "error_code" in view:
                return {
                    "state": "blocked",
                    "processed": len(results),
                    "results": results,
                    "blocked": view,
                }
            # The storage lock is released: intake and cancellation can continue during execution.
            try:
                executed = execution.execute(config, policy, view["run_id"], "")
            except Exception:
                # Do not copy provider/source exception text. If settlement fails, claimed remains.
                _settle(config, binding, view["run_id"], uncertain=True)
                raise
            result = _settle(config, binding, view["run_id"], uncertain=False)
            if "answer_rejection" in executed:
                result["answer_rejection"] = executed["answer_rejection"]
            results.append(result)
            if result["run_state"] != "succeeded" or result["batch_state"] == "cancelled":
                return {"state": "stopped", "processed": len(results), "results": results}
    return {"state": "limit_reached", "processed": len(results), "results": results}


def reconcile(config: Config, binding: Binding, run_id: str) -> dict[str, Any]:
    """Resolve stale dispatch claims from durable state only. Never execute, stop or replay."""
    with worker_lock(config), execution.run_lock(config, run_id):
        view = status.synchronize(config, binding, run_id)
        if view["dispatch_phase"] not in {"claimed", "unknown"}:
            return view
        return _settle(config, binding, run_id, uncertain=False)
