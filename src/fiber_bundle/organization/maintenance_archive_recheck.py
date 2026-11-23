"""Bounded worker retries for read-only verification of a committed archive."""

import time
from typing import Any

from .. import cloud_observation
from ..config import IntegrityError
from . import maintenance_archive, maintenance_archive_finalization, store

DELAYS = (1, 3, 9)


def due(job: Any) -> bool:
    return not job.get("archive_recheck") or job["archive_recheck"]["next_at"] <= time.time()


def schedule(db: Any, job: Any, item: Any, error: Exception) -> bool:
    if (
        not isinstance(error, cloud_observation.ObservationError)
        or not job.get("confirmed")
        or not item
        or not item.get("archive_operation")
        or error.detail["phase"] not in {"archive_finalize_inventory", "archive_committed_verify"}
        or error.detail["reason"] != "directory_changed"
        or not error.detail["retryable_read"]
        or not set(error.detail["changed_fields"]) <= {"mtime_ns", "ctime_ns"}
    ):
        return False
    operation = item["archive_operation"]
    previous = job.get("archive_recheck", {})
    attempt = previous.get("attempt", 0)
    if attempt >= len(DELAYS) or previous.get("operation", operation) != operation:
        return False
    bound = maintenance_archive_finalization.owned_operations(db, job)
    if not any(
        row["id"] == operation and row["state"] == "reconcile" and value.get("metadata_committed")
        for row, value, _ in bound
    ):
        return False
    job.update(
        archive_recheck={
            "operation": operation,
            "attempt": attempt + 1,
            "next_at": time.time() + DELAYS[attempt],
        },
        state="running",
        error=None,
        error_detail=error.detail,
        index_state="pending",
    )
    return True


def advance(config: Any, db: Any, job: Any) -> Any:
    if not due(job):
        return job
    operation = job["archive_recheck"]["operation"]
    bound = maintenance_archive_finalization.owned_operations(db, job)
    selected = next((entry for entry in bound if entry[0]["id"] == operation), None)
    if selected is None:
        raise IntegrityError("maintenance_archive_binding_changed")
    row, value, item = selected
    if not value.get("metadata_committed") or row["state"] not in {
        "reconcile",
        "applying",
        "completed",
    }:
        raise IntegrityError("placement_committed_evidence_missing")
    try:
        maintenance_archive_finalization.resume(config, db, job, operation_ids={operation})
        if item["phase"] != "archived":
            # A crash can persist the completed operation before the task phase.
            # apply's completed branch observes it without repeating the move.
            maintenance_archive.apply(config, db, job, item)
    except Exception as error:
        if schedule(db, job, item, error):
            return job
        job.pop("archive_recheck")
        job.pop("error_detail", None)
        if isinstance(error, cloud_observation.ObservationError):
            job["error_detail"] = error.detail
        job.update(state="partial", error=str(error)[:180])
        item.update(state="failed", error=job["error"])
    else:
        job.pop("archive_recheck")
        job.pop("error_detail", None)
    # Make the original item's updated phase durable before any other item runs.
    store.put(config, "jobs", job["id"], job["vault_id"], job)
    return job
