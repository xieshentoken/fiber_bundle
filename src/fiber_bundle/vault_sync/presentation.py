"""Local status/backup projections. These are never write authorization."""

import json
import sqlite3
import time
from typing import Any

from ..config import Config, InvalidInput, StorageError
from . import problems, progress, store


def observation(config: Config, identity: str) -> dict[str, Any] | None:
    value = store.status(config, identity)
    if value is None:
        return None
    value["observer_available"] = time.time() - value["heartbeat"] < 45
    if not value["observer_available"]:
        value["state"] = "observer_unavailable"
    return value


def index_status(config: Config, identity: str) -> dict[str, Any] | None:
    sync = observation(config, identity)
    if sync is None:
        return None
    from ..note_search_summary import snapshot

    cached = snapshot(config, identity)
    ready = (
        cached["generation_state"] == "ready"
        and sync["observer_available"]
        and sync["state"] == "ready"
        and not sync["pending"]
        and sync["baseline"]
        and sync["indexed"] == sync["generation"]
        and not sync["error"]
        and not sync["needs_attention"]
    )
    try:
        sync.update(progress.details(config, identity))
    except (OSError, ValueError, sqlite3.Error, StorageError):
        sync["detail_unavailable"] = True
    state = "ready" if ready else "stale"
    if sync["error"] or not sync["observer_available"]:
        state = "failed" if sync["error"] == "vault_sync_index_capacity" else "waiting"
    elif sync["active_pending"] or not sync["baseline"]:
        state = "pending"
    elif sync["indexed"] != sync["generation"] or sync["state"] == "index_pending":
        state = "building" if (cached.get("update") or {}).get("state") == "building" else "pending"
    elif sync["needs_attention"]:
        state = "partial"
    retry_times = [
        item["next_check"]
        for item in sync.get("issues", [])
        if problems.category(item["reason"]) != "review"
    ]
    if sync["next_check"] is not None:
        retry_times.append(sync["next_check"])
    if sync["error"]:
        retry_times.append(sync["index_after"])
    sync["next_retry"] = min(retry_times) if retry_times else None
    return {
        "state": state,
        "coverage_complete": ready,
        "index_mode": "persistent_chunks",
        "notes": cached["indexed_notes"],
        "built_at": cached["built_at"],
        "sync": sync,
    }


def backup_items(config: Config, identity: str) -> dict[str, list[Any]] | None:
    status = observation(config, identity)
    if status is None:
        return None
    if not status["baseline"] or status["pending"] or not status["observer_available"]:
        raise InvalidInput("backup_vault_sync_pending")
    with store.connection(config) as db:
        assert db is not None
        result = {}
        for row in db.execute(
            "SELECT path,signature,size FROM files WHERE vault=? AND directory=0", (identity,)
        ):
            sig = json.loads(row["signature"])
            # Keep the pre-existing backup trigger representation and growth accounting.
            result["vault:" + identity + ":" + row["path"]] = [
                str(sig[5]) + ":" + str(sig[1]) + ":" + str(sig[6]),
                row["size"],
            ]
        return result
