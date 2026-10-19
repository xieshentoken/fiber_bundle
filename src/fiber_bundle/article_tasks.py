"""Bound article tool orchestration; capture bytes stay outside the Pi workspace."""

import hashlib
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import article_bindings, article_worker, database, execution_ledger
from .article_capture import read_bounded, verify_capture
from .article_worker import CapturePolicy
from .config import Config, InvalidInput, contained
from .execution_config import ExecutionConfig


def output_path(config: Config, task: sqlite3.Row) -> Path:
    return contained(config.state_root, "article-captures/" + task["capture_task_id"] + "/output")


def summary(task: sqlite3.Row) -> dict[str, Any]:
    if task["state"] != "captured":
        raise InvalidInput("article capture not complete")
    return article_bindings.status(task)


def active(
    config: Config, db: sqlite3.Connection, run_id: str, policy: ExecutionConfig
) -> sqlite3.Row:
    run = execution_ledger.get_run(db, run_id)
    if run["state"] != "running" or run["config_digest"] != policy.digest:
        raise InvalidInput("article execution not active")
    task = article_bindings.get(db, run_id)
    if task is None:
        raise InvalidInput("execution is not an article task")
    execution_ledger.authorize(config, db, run)
    return task


def capture_bound(
    config: Config, policy: ExecutionConfig, run_id: str, body: object, *, deadline: float
) -> dict[str, Any]:
    if body != {}:
        raise InvalidInput("article tool accepts no caller parameters")

    def guard() -> None:
        if time.monotonic() >= deadline:
            raise InvalidInput("article execution deadline exceeded")
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                active(config, db, run_id, policy)
            finally:
                db.close()

    guard()
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            with database.transaction(db):
                task = active(config, db, run_id, policy)
                cached = task["state"] == "captured"
                if not cached:
                    if task["state"] != "queued":
                        raise InvalidInput("capture already attempted; explicit new task required")
                    db.execute(
                        "UPDATE article_tasks SET state='capturing' WHERE run_id=?", (run_id,)
                    )
        finally:
            db.close()
    output = output_path(config, task)
    if cached:
        if (
            hashlib.sha256(read_bounded(output, "capture.json", 65536)).hexdigest()
            != task["manifest_sha256"]
        ):
            raise InvalidInput("capture manifest changed")
        verify_capture(output, task["source_url"])
        guard()
        return summary(task)
    try:
        result = article_worker.execute_capture(
            config,
            CapturePolicy(task["worker_image"], task["worker_timeout"]),
            task["source_url"],
            guard=guard,
            task_id=task["capture_task_id"],
        )
        if result.directory != output or result.task_id != task["capture_task_id"]:
            raise InvalidInput("worker returned different capture scope")
        digest = hashlib.sha256(read_bounded(output, "capture.json", 65536)).hexdigest()
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                with database.transaction(db):
                    active(config, db, run_id, policy)
                    db.execute(
                        "UPDATE article_tasks SET state='captured',manifest_sha256=?,image_count=?,"
                        "markdown_bytes=?,error_code=NULL WHERE run_id=? AND state='capturing'",
                        (
                            digest,
                            len(result.verified.draft.images),
                            len(result.verified.draft.markdown.encode()),
                            run_id,
                        ),
                    )
                    completed = article_bindings.get(db, run_id)
                    assert completed is not None
                    return summary(completed)
            finally:
                db.close()
    except BaseException:
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                db.execute(
                    "UPDATE article_tasks SET state='failed',error_code='capture_failed' "
                    "WHERE run_id=? AND state='capturing'",
                    (run_id,),
                )
            finally:
                db.close()
        raise


def _interrupt(db: sqlite3.Connection, run_id: str) -> sqlite3.Row | None:
    task = article_bindings.get(db, run_id)
    if task is None or task["state"] not in ("capturing", "interrupted", "failed"):
        return None
    db.execute(
        "UPDATE article_tasks SET state='interrupted',error_code='capture_interrupted' "
        "WHERE run_id=?",
        (run_id,),
    )
    return task


def stop_locked(config: Config, db: sqlite3.Connection, run_id: str) -> None:
    """Caller holds the main lock and exact run ownership; avoid taking the lock twice."""
    task = _interrupt(db, run_id)
    if task is not None:
        article_worker.remove_container("fiber-capture-" + task["capture_task_id"])


def stop(config: Config, run_id: str) -> None:
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            task = _interrupt(db, run_id)
        finally:
            db.close()
    if task is not None:
        article_worker.remove_container("fiber-capture-" + task["capture_task_id"])
