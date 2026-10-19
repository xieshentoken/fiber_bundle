"""Unknown transport outcomes and explicit, audited retry authorization."""

import json
import time
from typing import Any

from ..config import InvalidInput
from . import manual_resolution, store


class UnknownCall(InvalidInput):
    def __init__(self, detail: dict[str, Any]):
        super().__init__("organization_model_result_unknown")
        self.detail = detail


def detail(db: Any, key: str, cached: Any) -> dict[str, Any]:
    row = db.execute(
        "SELECT state,error_code,created_at,policy_json FROM model_configuration_calls WHERE id=?",
        (cached.get("id", ""),),
    ).fetchone()
    return {
        "cache_key": key,
        "call_id": cached.get("id"),
        "created_at": row["created_at"] if row else cached.get("created_at"),
        "state": row["state"] if row else "unknown",
        "timeout_seconds": json.loads(row["policy_json"]).get("timeout_seconds", 120)
        if row
        else 120,
        "reason": row["error_code"] if row else cached.get("error_code"),
    }


def authorize(config: Any, db: Any, job: Any) -> None:
    """User explicitly accepts possible additional consumption; never release old reservations."""
    handled = manual_resolution.resolved(config, job)
    unknown = [
        i
        for i in job["items"]
        if i.get("analysis_error") == "organization_model_result_unknown"
        and i.get("entity") not in handled
    ]
    rows = []
    for item in unknown:
        info = item.get("analysis_detail", {})
        key = info.get("cache_key")
        prior = store.get(config, "model_reviews", key) if key else None
        if not prior or prior.get("id") != info.get("call_id"):
            raise InvalidInput("organization_model_call_changed")
        if prior["state"] != "succeeded":
            current = detail(db, key, prior)
            # A stale admission can be retried explicitly after its timeout plus
            # grace period, with the unknown original retained in attempt history.
            if (
                current["state"] == "running"
                and time.time() - (current["created_at"] or time.time())
                < current["timeout_seconds"] + 60
            ):
                raise InvalidInput("organization_model_call_inflight")
        rows.append((item, key, prior))
    if not rows:
        raise InvalidInput("organization_model_unknown_item_required")
    for item, key, prior in rows:
        if prior["state"] != "succeeded":
            prior.update(
                state="retry_authorized",
                retry_number=prior.get("retry_number", 0) + 1,
                retry_authorized_at=time.time(),
                retry_authorized_by=job["client_id"],
            )
            store.put(config, "model_reviews", key, job["vault_id"], prior)
        item.update(evaluated=False, phase="analysis")
        item.pop("analysis_error", None)
        item.pop("analysis_detail", None)
