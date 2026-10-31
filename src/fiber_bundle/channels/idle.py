"""Read-only work hints; claims always repeat authorization and selection under lock."""

from ..config import Config
from . import store
from .services import policy


def query_pending(config: Config, identity: str, revision: int, *, independent: bool) -> bool:
    with store.readonly(config) as db:
        if db is None:
            return False
        if not store.schema_ready(db):
            return True  # Let the locked writable connection apply additive migrations.
        channel = store.get(db, identity)
        service = policy.get(config, identity, "query")
        states = (
            {"running", "paused", "stopping", "error"}
            if independent and service is not None
            else {"running"}
        )
        if channel["revision"] != revision or channel["state"] not in states:
            return True  # Preserve the authoritative withdrawal error, even without work.
        # Only checkpointed model queries can resume an existing processing claim.
        resumable = channel["value"]["answer_mode"] == "model"
        return (
            db.execute(
                "SELECT 1 FROM events WHERE channel_id=? AND revision=? "
                "AND (state='captured' OR (? AND state='processing')) AND created>=? LIMIT 1",
                (identity, revision, resumable, service["admit_after"] if service else 0),
            ).fetchone()
            is not None
        )


def delivery_pending(config: Config, identity: str, revision: int) -> bool:
    with store.readonly(config) as db:
        if db is None:
            return False
        if not store.schema_ready(db):
            return True
        channel = store.get(db, identity)
        if channel["revision"] != revision or channel["state"] != "running":
            return True
        service = policy.get(config, identity, "query")
        # Unknown/sending rows must still reach the existing reconciliation guard,
        # regardless of their revision or the service's admission watermark.
        return (
            db.execute(
                "SELECT 1 FROM events WHERE channel_id=? AND (state IN ('sending','unknown') "
                "OR (state='ready' AND revision=? AND created>=?)) LIMIT 1",
                (identity, revision, service["admit_after"] if service else 0),
            ).fetchone()
            is not None
        )
