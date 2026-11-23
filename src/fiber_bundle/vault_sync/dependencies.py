"""Durable index dependency waits, independent of transient IO retry cooldowns."""

from typing import Any

from .. import database, note_search_jobs, operation_dependencies
from ..config import Config
from ..inputs import canonical
from . import store


def current(config: Config, identity: str) -> dict[str, Any] | None:
    with database.connect(config.state_root, readonly=True) as db:
        return operation_dependencies.describe(config, db, identity)


def refresh(config: Config, identity: str) -> bool:
    """Caller holds the storage lease. Recheck dependencies even during a cooldown."""
    value = current(config, identity)
    with store.connection(config, write=True) as side:
        assert side is not None
        side.execute(
            "CREATE TABLE IF NOT EXISTS index_dependencies "
            "(vault TEXT PRIMARY KEY,value TEXT NOT NULL) STRICT"
        )
        old = side.execute(
            "SELECT value FROM index_dependencies WHERE vault=?", (identity,)
        ).fetchone()
        if value:
            encoded = canonical(value)
            if old is None or old[0] != encoded:
                side.execute(
                    "INSERT OR REPLACE INTO index_dependencies VALUES(?,?)", (identity, encoded)
                )
                side.execute(
                    "UPDATE libraries SET state='waiting',"
                    "error='placement_reconciliation_required' "
                    "WHERE id=?",
                    (identity,),
                )
        elif old:
            side.execute("DELETE FROM index_dependencies WHERE vault=?", (identity,))
            side.execute(
                "UPDATE libraries SET state='index_pending',error=NULL,"
                "index_after=0,index_attempts=0 "
                "WHERE id=? AND (error IS NULL OR error='placement_reconciliation_required')",
                (identity,),
            )
    if value and (old is None or old[0] != canonical(value)):
        note_search_jobs.record(
            config, identity, "waiting", reason="placement_reconciliation_required"
        )
    return value is not None
