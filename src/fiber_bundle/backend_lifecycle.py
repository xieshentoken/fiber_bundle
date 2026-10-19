"""An explicitly started API process owns its lifetime independently of any window."""

import fcntl
import json
import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

from . import database, query_job_launch, query_jobs
from .config import Config, InvalidInput, contained
from .query_policy_store import write_private


@contextmanager
def process_lock(config: Config) -> Iterator[None]:
    fd = os.open(
        contained(config.state_root, "backend.lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InvalidInput("backend_already_running") from None
        yield
    finally:
        os.close(fd)


def observe(config: Config) -> dict[str, Any]:
    path = contained(config.state_root, "backend.json")
    if not path.exists():
        return {"state": "stopped", "managed": False}
    if path.stat().st_size > 4096:
        raise InvalidInput("backend_status_invalid")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict) or value.get("schema") != "backend-lifecycle/v1":
        raise InvalidInput("backend_status_invalid")
    fd = os.open(contained(config.state_root, "backend.lock"), os.O_RDWR | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return value
        return {**value, "state": "stopped"}
    finally:
        os.close(fd)


class BackendControl:
    """Only this process can request its own graceful shutdown; no PID signalling."""

    def __init__(self, config: Config, origin: str):
        self.config, self.origin = config, origin
        self.instance_id = str(uuid4())
        self.stop_event = threading.Event()
        self.state = "starting"
        self.recovery: dict[str, Any] = {"state": "pending"}

    def status(self) -> dict[str, Any]:
        return {
            "schema": "backend-lifecycle/v1",
            "state": self.state,
            "managed": True,
            "instance_id": self.instance_id,
            "origin": self.origin,
            "receiver": "external_not_managed",
            "recovery": self.recovery,
        }

    def record(self, state: str) -> None:
        self.state = state
        write_private(
            contained(self.config.state_root, "backend.json"), json.dumps(self.status()).encode()
        )

    def permit(self) -> None:
        if self.stop_event.is_set():
            raise InvalidInput("backend_stopping")

    def stop(self, db: sqlite3.Connection, instance_id: str) -> dict[str, Any]:
        if instance_id != self.instance_id:
            raise InvalidInput("backend_instance_conflict")
        self.stop_event.set()  # Prevent new admissions even if cancellation below fails.
        self.record("stopping")
        for row in db.execute(
            "SELECT id,client_id FROM query_jobs WHERE state IN ('reserved','running')"
        ).fetchall():
            active = db.execute(
                "SELECT active FROM clients WHERE id=?", (row["client_id"],)
            ).fetchone()
            if active is not None and active[0]:
                query_jobs.cancel(self.config, db, row["client_id"], row["id"])
            else:
                db.execute("UPDATE query_jobs SET state='cancelled' WHERE id=?", (row["id"],))
        return self.status()


def resume_safe(config: Config) -> dict[str, int]:
    """Resume only committed reserved admissions with no attempt; never replay running IO."""
    resumed = 0
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            rows = db.execute(
                "SELECT * FROM query_jobs WHERE state='reserved' ORDER BY created_at,id LIMIT 100"
            ).fetchall()
            for row in rows:
                table, key = (
                    ("local_query_tasks", "id")
                    if row["role"] == "local"
                    else ("execution_runs", "id")
                )
                task = db.execute(
                    f"SELECT state FROM {table} WHERE {key}=?", (row["task_id"],)
                ).fetchone()
                calls = db.execute(
                    "SELECT 1 FROM "
                    + (
                        "local_query_calls WHERE task_id=?"
                        if row["role"] == "local"
                        else "model_calls WHERE run_id=?"
                    ),
                    (row["task_id"],),
                ).fetchone()
                active = db.execute(
                    "SELECT active FROM clients WHERE id=?", (row["client_id"],)
                ).fetchone()
                if (
                    task is None
                    or task[0] != "queued"
                    or calls is not None
                    or active is None
                    or not active[0]
                ):
                    continue
                query_job_launch.launch(config, row["client_id"], row["id"])
                resumed += 1
        finally:
            db.close()
    from .expert_recovery import reconcile

    interrupted = reconcile(config)
    return {"resumed": resumed, "expert_interrupted": interrupted}
