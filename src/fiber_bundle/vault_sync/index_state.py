"""Local index visibility and freshness; never enumerate a Vault on a query/status read."""

import sqlite3
import time
from typing import Any

from ..config import Config
from . import store


def visibility(config: Config, db: sqlite3.Connection, vault: sqlite3.Row) -> str:
    from .visibility import read

    return read(config, db, vault).token


def token(config: Config, db: sqlite3.Connection, vault: sqlite3.Row) -> str | None:
    row = store.library(config, vault["id"])
    if row is None:
        return None
    from .. import note_search_cache as cache

    if not row["baseline"] or time.time() - row["heartbeat"] >= 45:
        raise cache.IndexUnavailable("note_index_update_required")
    with cache.connection(config, vault["id"]) as index:
        if (
            index is None
            or not index.execute(
                "SELECT 1 FROM sqlite_master WHERE name='sync_metadata'"
            ).fetchone()
        ):
            request(config, vault["id"])
            raise cache.IndexUnavailable("note_index_update_required")
        current = index.execute("SELECT * FROM sync_metadata").fetchone()
        if current is None or current["visibility"] != visibility(config, db, vault):
            request(config, vault["id"])
            raise cache.IndexUnavailable("note_index_update_required")
        return str(index.execute("SELECT fingerprint FROM generation").fetchone()[0])


def request(config: Config, identity: str) -> None:
    with store.connection(config, write=True) as db:
        assert db is not None
        db.execute("UPDATE libraries SET state='index_pending' WHERE id=?", (identity,))


def coverage(config: Config, identity: str) -> dict[str, Any] | None:
    row = store.status(config, identity)
    if row is None:
        return None
    with store.connection(config) as db:
        issues = (
            db.execute("SELECT count(*) FROM issues WHERE vault=?", (identity,)).fetchone()[0]
            if db
            else 0
        )
    return {
        "complete": bool(row["baseline"])
        and not row["pending"]
        and row["indexed"] == row["generation"]
        and not issues
        and time.time() - row["heartbeat"] < 45,
        "pending": row["pending"],
        "needs_attention": issues,
        "checked_at": row["checked_at"],
    }
