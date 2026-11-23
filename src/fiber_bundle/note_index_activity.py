"""Small, read-only index activity projection from local maintenance ledgers.

This is display evidence, never freshness or write authorization. It deliberately
does not open a Vault or its search index, or schedule maintenance while polled.
"""

import math
import sqlite3
import time
from typing import Any

from . import note_search_jobs
from .config import Config, StorageError
from .vault_sync import store

OBSERVER_AGE = 45
BUILD_AGE = 300
STATES = {"pending", "index_pending", "ready", "attention", "waiting"}


def _time(value: Any) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and value >= 0


def _valid(row: dict[str, Any]) -> bool:
    return (
        row["state"] in STATES
        and all(_time(row[k]) for k in ("heartbeat", "checked_at", "index_after"))
        and all(
            type(row[k]) is int and row[k] >= 0
            for k in ("generation", "pending_paths", "active_pending", "needs_attention")
        )
        and type(row["indexed"]) is int
        and -1 <= row["indexed"] <= row["generation"]
        and row["baseline"] in {0, 1}
    )


def _state(row: dict[str, Any], job: dict[str, Any] | None, now: float) -> str:
    if not 0 <= now - row["heartbeat"] < OBSERVER_AGE:
        return "unverified"
    # A previous failure is historical once newly observed files are being admitted.
    if row["active_pending"] or not row["baseline"]:
        return "observing"
    if job and job["state"] == "building":
        return "building" if 0 <= now - job["updated_at"] <= BUILD_AGE else "unverified"
    if row["error"]:
        return "failed" if row["error"] == "vault_sync_index_capacity" else "waiting"
    changed = row["indexed"] != row["generation"] or row["state"] == "index_pending"
    if row["pending_paths"] or row["state"] == "waiting" or (changed and row["index_after"] > now):
        return "waiting"
    if changed:
        return "queued"
    if job is None or job["state"] in {"stale", "interrupted"}:
        return "unverified"
    if job["state"] == "failed":
        return "failed"
    if job["state"] == "waiting":
        return "waiting"
    if job["state"] == "pending":
        return "queued"
    if row["needs_attention"]:
        return "needs_review"
    if row["state"] == "ready" and job["state"] == "ready":
        return "ready"
    return "unverified"


def snapshot(config: Config, db: sqlite3.Connection) -> dict[str, Any]:
    now = time.time()
    vaults = db.execute(
        "SELECT id,name FROM vaults WHERE status='active' AND attachment_state='attached' "
        "ORDER BY name,id"
    ).fetchall()
    rows: dict[str, dict[str, Any]] = {}
    try:
        with store.connection(config) as side:
            if side is not None:
                side.execute("PRAGMA busy_timeout=300")
                side.execute("BEGIN")
                for vault in vaults:
                    row = side.execute(
                        "SELECT state,generation,indexed,baseline,heartbeat,checked_at,"
                        "error,index_after,"
                        "(SELECT count(*) FROM pending WHERE vault=l.id) AS pending_paths,"
                        "(SELECT count(*) FROM pending WHERE vault=l.id AND attempts=0) "
                        "AS active_pending,"
                        "(SELECT count(*) FROM issues WHERE vault=l.id) AS needs_attention "
                        "FROM libraries l WHERE id=?",
                        (vault["id"],),
                    ).fetchone()
                    if row is not None:
                        rows[vault["id"]] = dict(row)
    except (OSError, sqlite3.Error, StorageError):
        # A missing/unsupported/busy side ledger must not claim the index is ready.
        rows.clear()
    result = []
    for vault in vaults:
        item: dict[str, Any] = {
            "vault_id": vault["id"],
            "name": vault["name"],
            "state": "unverified",
            "pending_paths": None,
            "needs_attention": None,
            "updated_at": None,
        }
        try:
            job = note_search_jobs.read(config, vault["id"])
            row = rows.get(vault["id"])
            if row is not None and _valid(row):
                from .vault_sync import explicit_refresh

                activity = explicit_refresh.activity(config, vault["id"])
                row["needs_attention"] += len(explicit_refresh.package_issues(config, vault["id"]))
                item.update(
                    state=_state(row, job, now),
                    pending_paths=row["pending_paths"],
                    needs_attention=row["needs_attention"],
                    updated_at=max(row["checked_at"], job["updated_at"] if job else 0) or None,
                )
                if activity and activity["stage"] != "index" and item["state"] != "unverified":
                    item.update(state=activity["state"], phase=activity["stage"])
        except (OSError, ValueError, TypeError, sqlite3.Error, StorageError):
            # An unreadable job record is isolated to its own Vault.
            pass
        from .operation_dependencies import describe

        dependency = describe(config, db, vault["id"])
        if dependency:
            item.update(state="blocked", dependency=dependency)
            item.pop("phase", None)
        result.append(item)
    return {"schema": "note-index-activity/v1", "observed_at": now, "vaults": result}
