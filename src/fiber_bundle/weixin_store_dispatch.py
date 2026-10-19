"""Bounded Store scheduling with persistent attempts and explicit failed-work recovery."""

import fcntl
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from . import database
from . import weixin_batches as batches
from . import weixin_inbox as inbox
from . import weixin_store as handoff
from .config import Config, InvalidInput, contained
from .weixin_events import Binding


@contextmanager
def worker_lock(config: Config) -> Iterator[None]:
    config.validate()
    fd = os.open(
        contained(config.state_root, "weixin-store-worker.lock"),
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InvalidInput("Store worker already active") from None
        yield
    finally:
        os.close(fd)


def limit(value: int) -> int:
    if type(value) is not int or not 0 <= value <= 100:
        raise InvalidInput("Store limit must be an integer from 0 to 100")
    return value


def initialize(config: Config, binding: Binding) -> None:
    with inbox.connection(config, binding) as db, database.transaction(db):
        inbox.bound(db, binding)
        db.execute(
            "CREATE TABLE IF NOT EXISTS wx_store_attempts("
            "id INTEGER PRIMARY KEY,account_id TEXT NOT NULL,"
            "batch_id TEXT NOT NULL REFERENCES wx_batches(id),"
            "state TEXT NOT NULL CHECK(state IN ('running','failed','stored'))) STRICT"
        )


def _execute(config: Config, binding: Binding, batch_id: str, attempt: int) -> None:
    try:
        result = handoff.transfer(config, binding, batch_id)
        if result["store_state"] != "stored":
            raise InvalidInput("Store transfer not complete")
    except Exception:
        # If even this settlement fails, running remains unresolved and blocks automatic retry.
        with inbox.connection(config, binding) as db, database.transaction(db):
            inbox.bound(db, binding)
            db.execute("UPDATE wx_store_attempts SET state='failed' WHERE id=?", (attempt,))
        raise
    with inbox.connection(config, binding) as db, database.transaction(db):
        inbox.bound(db, binding)
        db.execute("UPDATE wx_store_attempts SET state='stored' WHERE id=?", (attempt,))


def drain(
    config: Config,
    binding: Binding,
    *,
    maximum: int,
    reserve: Callable[[sqlite3.Connection, str], bool] | None = None,
    eligible: Callable[[str], bool] | None = None,
) -> int:
    if limit(maximum) == 0:
        return 0
    with worker_lock(config):
        return _drain(config, binding, maximum=maximum, reserve=reserve, eligible=eligible)


def _drain(
    config: Config,
    binding: Binding,
    *,
    maximum: int,
    reserve: Callable[[sqlite3.Connection, str], bool] | None = None,
    eligible: Callable[[str], bool] | None = None,
) -> int:
    """Budget counts batches, not member assets. Failed/running work needs explicit resume."""
    if limit(maximum) == 0:
        return 0
    initialize(config, binding)
    completed = 0
    for _ in range(maximum):
        with inbox.connection(config, binding) as db, database.transaction(db):
            inbox.bound(db, binding)
            rows = db.execute(
                "SELECT b.id,a.state FROM wx_batches b LEFT JOIN wx_store_attempts a "
                "ON a.id=(SELECT MAX(x.id) FROM wx_store_attempts x WHERE x.batch_id=b.id) "
                "WHERE b.account_id=? AND b.state='selected' ORDER BY b.rowid",
                (binding.account_id,),
            ).fetchall()
            selected = None
            for row in rows:
                if eligible is not None and not eligible(row["id"]):
                    continue
                if row["state"] in {"running", "failed"}:
                    raise InvalidInput("Store attempt requires explicit recovery")
                if row["state"] == "stored":
                    continue
                view = batches.snapshot(db, binding, row["id"])
                if view["needs_question"] or not view["content_ready"]:
                    continue
                # A direct successful transfer is already complete; do not enqueue it again.
                if db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='wx_store_handoffs'"
                ).fetchone():
                    saved = db.execute(
                        "SELECT state FROM wx_store_handoffs WHERE batch_id=?", (row["id"],)
                    ).fetchone()
                    if saved is not None:
                        if saved["state"] == "stored":
                            continue
                        raise InvalidInput("partial Store handoff requires explicit recovery")
                if selected is None:
                    selected = row["id"]
            if selected is None:
                break
            if reserve is not None and not reserve(db, selected):
                break
            cursor = db.execute(
                "INSERT INTO wx_store_attempts(account_id,batch_id,state) VALUES(?,?,'running')",
                (binding.account_id, selected),
            )
            attempt = int(cursor.lastrowid or 0)
        _execute(config, binding, selected, attempt)
        completed += 1
    return completed


def resume(config: Config, binding: Binding, batch_id: str) -> dict[str, Any]:
    with worker_lock(config):
        return _resume(config, binding, batch_id)


def _resume(config: Config, binding: Binding, batch_id: str) -> dict[str, Any]:
    """Explicit recovery appends an attempt; it never erases prior failures."""
    initialize(config, binding)
    with inbox.connection(config, binding) as db, database.transaction(db):
        inbox.bound(db, binding)
        view = batches.snapshot(db, binding, batch_id)
        if view["state"] != "selected" or not view["content_ready"] or view["needs_question"]:
            raise InvalidInput("batch cannot resume Store transfer")
        previous = db.execute(
            "SELECT state FROM wx_store_attempts WHERE batch_id=? ORDER BY id DESC LIMIT 1",
            (batch_id,),
        ).fetchone()
        if previous is None:
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='wx_store_handoffs'"
            ).fetchone()
            partial = (
                db.execute(
                    "SELECT state FROM wx_store_handoffs WHERE batch_id=?", (batch_id,)
                ).fetchone()
                if exists
                else None
            )
            if partial is None or partial["state"] != "prepared":
                raise InvalidInput("no interrupted Store handoff")
        if previous is not None and previous["state"] == "stored":
            raise InvalidInput("Store attempt already completed")
        cursor = db.execute(
            "INSERT INTO wx_store_attempts(account_id,batch_id,state) VALUES(?,?,'running')",
            (binding.account_id, batch_id),
        )
        attempt = int(cursor.lastrowid or 0)
    _execute(config, binding, batch_id, attempt)
    return handoff.get(config, binding, batch_id)


def attempts(config: Config, binding: Binding, batch_id: str) -> list[dict[str, Any]]:
    """Latest 100 attempt states; no source content or exception text."""
    initialize(config, binding)
    with inbox.connection(config, binding) as db:
        inbox.bound(db, binding)
        batches.snapshot(db, binding, batch_id)
        return [
            dict(row)
            for row in db.execute(
                "SELECT id,state FROM wx_store_attempts WHERE account_id=? AND batch_id=? "
                "ORDER BY id DESC LIMIT 100",
                (binding.account_id, batch_id),
            )
        ]
