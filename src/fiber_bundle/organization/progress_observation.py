"""Current local index observation beside immutable task outcomes; never resumes work."""

import sqlite3
import time
from typing import Any

from ..config import Config, StorageError
from ..vault_sync.presentation import index_status


def observe(config: Config, vault: str) -> dict[str, Any] | None:
    try:
        status = index_status(config, vault)
    except (OSError, ValueError, sqlite3.Error, StorageError):
        return {"state": "unknown", "observed_at": time.time()}
    if status is None:
        return None
    sync = status["sync"]
    return {
        "state": status["state"],
        "observed_at": time.time(),
        "built_at": status.get("built_at"),
        "checked_at": sync.get("checked_at"),
        "generation": sync["generation"],
        "indexed_generation": sync["indexed"],
        "notes": status.get("notes"),
        "observer_available": sync["observer_available"],
    }


def project(job: Any, observed: Any) -> Any:
    value = dict(job)
    last = job.get("updated_at") or (job.get("activity") or {}).get("updated_at")
    value["last_progress_at"] = last
    if observed:
        value["index_observation"] = {
            **observed,
            "after_task": bool(last and (observed.get("built_at") or 0) >= last),
        }
    # A frozen omission is evidence of the preview's scope, not a live download status.
    if value.get("coverage"):
        value["coverage"] = {**value["coverage"], "historical": True}
    return value
