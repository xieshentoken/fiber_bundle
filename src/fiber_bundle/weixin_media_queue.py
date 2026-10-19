"""Durable explicit download attempts; no implicit retries or network access."""

import fcntl
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

from . import database
from . import weixin_inbox as inbox
from . import weixin_recovery as recovery
from .config import Config, InvalidInput, contained
from .weixin_events import Binding


@contextmanager
def worker_lock(config: Config) -> Iterator[None]:
    config.validate()
    fd = os.open(
        contained(config.state_root, "weixin-media-worker.lock"),
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InvalidInput("media worker already active") from None
        yield
    finally:
        os.close(fd)


def initialize(config: Config, binding: Binding) -> None:
    recovery.initialize(config, binding)
    with inbox.connection(config, binding) as db, database.transaction(db):
        inbox.bound(db, binding)
        db.execute(
            "CREATE TABLE IF NOT EXISTS wx_download_attempts("
            "id TEXT PRIMARY KEY,account_id TEXT NOT NULL,"
            "job_id TEXT NOT NULL REFERENCES events(job_id),"
            "item_key TEXT NOT NULL,raw_sha256 TEXT NOT NULL,attempt INTEGER NOT NULL,"
            "state TEXT NOT NULL CHECK(state IN "
            "('queued','running','succeeded','failed','interrupted')),"
            "error_code TEXT,UNIQUE(account_id,job_id,item_key,attempt)) STRICT"
        )


def public(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in ("id", "job_id", "item_key", "attempt", "state", "error_code")}


def checked(db: sqlite3.Connection, binding: Binding, attempt_id: str) -> sqlite3.Row:
    row = db.execute(
        "SELECT * FROM wx_download_attempts WHERE account_id=? AND id=?",
        (binding.account_id, attempt_id),
    ).fetchone()
    if not isinstance(row, sqlite3.Row):
        raise InvalidInput("unknown download attempt")
    return row


def ready(
    db: sqlite3.Connection, binding: Binding, row: sqlite3.Row, *, require_active: bool = True
) -> bool:
    event, present, required, _ = recovery.load(db, binding, row["job_id"])
    if require_active:
        recovery.ensure_active(db, binding, event["event_id"])
    if event["raw_sha256"] != row["raw_sha256"] or row["item_key"] not in required:
        raise InvalidInput("download attempt identity changed")
    return row["item_key"] in present


def insert(
    db: sqlite3.Connection, binding: Binding, job: str, key: str, sha: str, number: int
) -> dict[str, Any]:
    attempt_id = str(uuid4())
    db.execute(
        "INSERT INTO wx_download_attempts VALUES(?,?,?,?,?,?,'queued',NULL)",
        (attempt_id, binding.account_id, job, key, sha, number),
    )
    return public(checked(db, binding, attempt_id))


def enqueue(
    config: Config, binding: Binding, job_id: str, *, maximum: int | None = None
) -> list[dict[str, Any]]:
    """Queue missing positions for one explicit event. Existing failures remain failures."""
    if maximum is not None and (type(maximum) is not int or not 1 <= maximum <= 100):
        raise InvalidInput("media enqueue limit must be between 1 and 100")
    with inbox.connection(config, binding) as db, database.transaction(db):
        inbox.bound(db, binding)
        event, present, required, _ = recovery.load(db, binding, job_id)
        recovery.ensure_active(db, binding, event["event_id"])
        result = []
        missing = sorted(required - present.keys())
        for key in missing if maximum is None else missing[:maximum]:
            latest = db.execute(
                "SELECT * FROM wx_download_attempts WHERE account_id=? AND job_id=? AND item_key=? "
                "ORDER BY attempt DESC LIMIT 1",
                (binding.account_id, job_id, key),
            ).fetchone()
            result.append(
                public(latest)
                if latest is not None
                else insert(db, binding, job_id, key, event["raw_sha256"], 1)
            )
        return result


def status(config: Config, binding: Binding, attempt_id: str) -> dict[str, Any]:
    with inbox.connection(config, binding) as db:
        inbox.bound(db, binding)
        return public(checked(db, binding, attempt_id))


def inspect_claim(config: Config, binding: Binding, attempt_id: str) -> None:
    with inbox.connection(config, binding) as db:
        inbox.bound(db, binding)
        row = checked(db, binding, attempt_id)
        if row["state"] != "running":
            raise InvalidInput("download attempt is not running")
        ready(db, binding, row)


def list_attempts(config: Config, binding: Binding, *, after: int = 0) -> dict[str, Any]:
    if type(after) is not int or after < 0:
        raise InvalidInput("invalid download page")
    with inbox.connection(config, binding) as db:
        inbox.bound(db, binding)
        rows = db.execute(
            "SELECT rowid,* FROM wx_download_attempts WHERE account_id=? AND rowid>? "
            "ORDER BY rowid LIMIT 101",
            (binding.account_id, after),
        ).fetchall()
        return {
            "attempts": [public(row) for row in rows[:100]],
            "next_cursor": rows[99]["rowid"] if len(rows) > 100 else None,
        }


def retry(config: Config, binding: Binding, attempt_id: str) -> dict[str, Any]:
    """Explicit new attempt only. The previous failed/interrupted record is retained."""
    with inbox.connection(config, binding) as db, database.transaction(db):
        inbox.bound(db, binding)
        row = checked(db, binding, attempt_id)
        if row["state"] not in {"failed", "interrupted"}:
            raise InvalidInput("download attempt is not retryable")
        if ready(db, binding, row):
            raise InvalidInput("attachment already recovered; no retry needed")
        latest = db.execute(
            "SELECT * FROM wx_download_attempts WHERE account_id=? AND job_id=? AND item_key=? "
            "ORDER BY attempt DESC LIMIT 1",
            (binding.account_id, row["job_id"], row["item_key"]),
        ).fetchone()
        if latest["id"] != attempt_id:
            return public(latest)  # repeated retry command cannot create another attempt
        return insert(
            db, binding, row["job_id"], row["item_key"], row["raw_sha256"], row["attempt"] + 1
        )


def claim(
    config: Config, binding: Binding, *, attempt_id: str | None = None
) -> dict[str, Any] | None:
    with inbox.connection(config, binding) as db, database.transaction(db):
        inbox.bound(db, binding)
        if db.execute(
            "SELECT 1 FROM wx_download_attempts WHERE account_id=? AND state='running'",
            (binding.account_id,),
        ).fetchone():
            raise InvalidInput("interrupted download requires reconciliation")
        if attempt_id is not None:
            row = checked(db, binding, attempt_id)
            if row["state"] != "queued":
                raise InvalidInput("download attempt is not queued")
        else:
            row = db.execute(
                "SELECT * FROM wx_download_attempts WHERE account_id=? AND state='queued' "
                "ORDER BY rowid LIMIT 1",
                (binding.account_id,),
            ).fetchone()
        if row is None:
            return None
        db.execute("UPDATE wx_download_attempts SET state='running' WHERE id=?", (row["id"],))
        return public(row)


def finish(
    config: Config, binding: Binding, attempt_id: str, state: str, error: str | None
) -> None:
    if (state, error) not in {
        ("succeeded", None),
        ("failed", "download_failed"),
        ("interrupted", "worker_interrupted"),
    }:
        raise InvalidInput("invalid download settlement")
    with inbox.connection(config, binding) as db, database.transaction(db):
        inbox.bound(db, binding)
        row = checked(db, binding, attempt_id)
        if row["state"] != "running":
            raise InvalidInput("download attempt is not running")
        if state == "succeeded" and not ready(db, binding, row):
            raise InvalidInput("download bytes missing at settlement")
        db.execute(
            "UPDATE wx_download_attempts SET state=?,error_code=? WHERE id=?",
            (state, error, attempt_id),
        )


def reconcile(config: Config, binding: Binding, attempt_id: str) -> dict[str, Any]:
    """With the worker stopped, inspect durable bytes; never issue a network request."""
    with worker_lock(config), inbox.connection(config, binding) as db, database.transaction(db):
        inbox.bound(db, binding)
        row = checked(db, binding, attempt_id)
        if row["state"] not in {"running", "interrupted"}:
            return public(row)
        complete = ready(db, binding, row, require_active=False)
        db.execute(
            "UPDATE wx_download_attempts SET state=?,error_code=? WHERE id=?",
            (
                "succeeded" if complete else "interrupted",
                None if complete else "worker_interrupted",
                attempt_id,
            ),
        )
        return public(checked(db, binding, attempt_id))
