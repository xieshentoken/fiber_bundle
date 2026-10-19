"""Persisted login policy. Existing sessions retain the expiry fixed at login."""

import sqlite3
from typing import Any

from . import database
from .config import InvalidInput


class Conflict(InvalidInput):
    pass


def inspect(db: sqlite3.Connection, default_ttl: int) -> dict[str, Any]:
    row = db.execute("SELECT session_ttl,revision FROM system_settings WHERE id=1").fetchone()
    return {
        "session_ttl": row[0] if row else default_ttl,
        "revision": row[1] if row else 0,
        "source": "saved" if row else "startup_default",
        "timing": "fixed_from_login",
        "effective": "next_login",
    }


def save(
    db: sqlite3.Connection, default_ttl: int, session_ttl: int, expected_revision: int
) -> dict[str, Any]:
    if type(session_ttl) is not int or not 1 <= session_ttl <= 86400:
        raise InvalidInput("system_session_ttl_invalid")
    with database.transaction(db):
        previous = inspect(db, default_ttl)
        if previous["revision"] != expected_revision:
            raise Conflict("system_settings_revision_conflict")
        db.execute(
            "INSERT INTO system_settings VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET "
            "session_ttl=excluded.session_ttl,revision=excluded.revision",
            (session_ttl, expected_revision + 1),
        )
    return inspect(db, default_ttl)
