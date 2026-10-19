"""Durable owner commands and a publication/abort ordering boundary."""

import fcntl
import json
import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from .config import Config, InvalidInput, contained


def initialize(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS fiber_intake_abort_commands (
        account_id TEXT NOT NULL, event_id TEXT NOT NULL, command_sha256 TEXT NOT NULL,
        state TEXT NOT NULL, scope TEXT NOT NULL CHECK(json_valid(scope)),
        value TEXT NOT NULL CHECK(json_valid(value)), receipt_state TEXT,
        created_at REAL NOT NULL, updated_at REAL NOT NULL,
        PRIMARY KEY(account_id,event_id)
    ) STRICT""")


def parse(text: str) -> tuple[bool, str | None]:
    parts = text.strip().split()
    if not parts or parts[0].casefold() not in {"abort", "/abort"}:
        return False, None
    if len(parts) == 1:
        return True, None
    if len(parts) == 2 and re.fullmatch(
        r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})?",
        parts[1],
    ):
        return True, parts[1].lower()
    return True, "invalid"


@contextmanager
def critical(config: Config) -> Iterator[None]:
    path = contained(config.state_root, "intake-abort.lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def requested(config: Config, context: dict[str, Any]) -> bool:
    try:
        return _requested(config, context)
    except sqlite3.Error as error:
        raise InvalidInput("intake_abort_store_unavailable") from error


def _requested(config: Config, context: dict[str, Any]) -> bool:
    path = contained(config.state_root, "weixin-inbox.sqlite3")
    if not path.exists():
        return False
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1) as db:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='fiber_intake_abort_commands'"
        ).fetchone():
            return False
        for (raw,) in db.execute(
            "SELECT scope FROM fiber_intake_abort_commands WHERE "
            "state IN ('requested','stopped') AND json_extract(scope,'$.channel')=?",
            (context.get("channel"),),
        ):
            scope = json.loads(raw)
            if scope.get("kind") == "job":
                if scope.get("job_id") == context.get("job_id"):
                    return True
            elif scope.get("batch") == context.get("batch"):
                if scope.get("kind") == "batch" or (
                    context.get("index") is not None and scope.get("index") == context["index"]
                ):
                    return True
    return False


def guard(config: Config, context: dict[str, Any]) -> None:
    if requested(config, context):
        raise InvalidInput("intake_user_aborted")
