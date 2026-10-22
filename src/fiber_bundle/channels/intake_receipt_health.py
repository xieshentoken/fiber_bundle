"""Read-only receipt observation; unknown sends remain local to their batch."""

from typing import Any

from ..config import Config
from . import store
from .services import policy as services


def summary(config: Config, identity: str, revision: int, enabled: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "enabled": enabled,
        "pending_batches": 0,
        "needs_review_batches": 0,
        "sending_batches": 0,
    }
    with store.readonly(config) as db:
        if db is None:
            return result
        rows = db.execute(
            "SELECT coalesce(json_extract(value,'$.receipt_state'),''),count(*) "
            "FROM channel_intake_records WHERE channel_id=? AND "
            "json_extract(value,'$.receipt_state') IN ('sending','unknown') GROUP BY 1",
            (identity,),
        ).fetchall()
        counts = dict(rows)
        result["needs_review_batches"] = counts.get("unknown", 0)
        result["sending_batches"] = counts.get("sending", 0)
        result["pending_batches"] = db.execute(
            "SELECT count(*) FROM channel_intake_records WHERE channel_id=? AND revision=? "
            "AND created>=? AND (state='stored' OR (state='needs_review' "
            "AND json_type(value,'$.failure')='object')) "
            "AND coalesce(json_extract(value,'$.receipt_state'),'') NOT IN ('sending','unknown') "
            "AND (json_extract(value,'$.receipt_state') IS NULL OR "
            "(json_extract(value,'$.receipt_tracking.version')=2 AND "
            "coalesce(json_extract(value,'$.receipt_tracking.done'),0)=0))",
            (identity, revision, services.cutoff(config, identity)),
        ).fetchone()[0]
    return result
