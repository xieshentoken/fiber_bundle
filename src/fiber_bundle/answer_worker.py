"""Bounded independent dispatcher for queued answer tasks, without automatic replay."""

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from . import answer_tasks, auth, database, execution
from .config import Config, InvalidInput, StorageError, contained
from .execution_config import ExecutionConfig


@contextmanager
def worker_lock(config: Config) -> Iterator[None]:
    config.validate()
    fd = os.open(
        contained(config.state_root, "answer-worker.lock"),
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InvalidInput("answer worker already active") from None
        yield
    finally:
        os.close(fd)


def dispatch(config: Config, policy: ExecutionConfig, *, max_tasks: int = 1) -> dict[str, Any]:
    """Process up to max_tasks in FIFO order; stop on idle, rejection or any failed execution."""
    policy.validate()
    if type(max_tasks) is not int or not 1 <= max_tasks <= 100:
        raise InvalidInput("worker task limit must be between 1 and 100")
    results: list[dict[str, Any]] = []
    with worker_lock(config):
        for _ in range(max_tasks):
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    run = db.execute(
                        "SELECT r.* FROM execution_runs r JOIN answer_tasks t ON t.run_id=r.id "
                        "WHERE r.state='queued' AND r.config_digest=? "
                        "AND NOT EXISTS (SELECT 1 FROM model_calls c WHERE c.run_id=r.id) "
                        "ORDER BY r.created_at,r.id LIMIT 1",
                        (policy.digest,),
                    ).fetchone()
                    if run is None:
                        return {"state": "idle", "processed": len(results), "results": results}
                    task = answer_tasks.get(db, run["id"])
                    assert task is not None
                    try:
                        answer_tasks.current(config, db, run, task)
                    except (auth.Denied, StorageError):
                        return {
                            "state": "blocked",
                            "processed": len(results),
                            "results": results,
                            "run_id": run["id"],
                            "error_code": "answer_preflight_rejected",
                        }
                    run_id = run["id"]
                finally:
                    db.close()
            # Do not hold the storage lock across model calls: revocation/cancellation must proceed.
            try:
                result = execution.execute(config, policy, run_id, "")
            except (auth.Denied, StorageError):
                return {
                    "state": "blocked",
                    "processed": len(results),
                    "results": results,
                    "run_id": run_id,
                    "error_code": "answer_dispatch_rejected",
                }
            results.append(result)
            if result["state"] != "succeeded":
                return {"state": "stopped", "processed": len(results), "results": results}
    return {"state": "limit_reached", "processed": len(results), "results": results}
