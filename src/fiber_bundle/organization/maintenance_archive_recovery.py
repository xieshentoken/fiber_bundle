"""Explicit recovery controls; original archive operations and undo evidence stay intact."""

import json
import time
from typing import Any

from ..config import IntegrityError, InvalidInput
from ..inputs import canonical
from ..objects import digest
from . import store
from .maintenance_archive_evidence import journal_proof, task_binding, untouched


def preview(config: Any, db: Any, job: Any) -> Any:
    if (
        job["state"] not in {"partial", "failed", "paused", "waiting_file"}
        or job.get("stage") == "rollback"
    ):
        raise InvalidInput("maintenance_close_pause_required")
    if any(i.get("intent") for i in job["items"]):
        raise IntegrityError("maintenance_close_recovery_required")
    candidates = [
        i
        for i in job["items"]
        if i.get("archive_operation")
        and not i.get("archive_deferral")
        and i.get("phase") == "members_done"
        and i["state"] != "done"
    ]
    if not candidates:
        raise InvalidInput("maintenance_archive_deferral_required")
    journal = journal_proof(config, db, job)
    items = [untouched(config, db, job, item, require_blocked=True) for item in candidates]
    token = digest(canonical([task_binding(job), journal, items]).encode())
    return {
        "plan_sha256": token,
        "items": items,
        "remaining": sum(i["state"] == "pending" for i in job["items"]),
    }


def apply(config: Any, db: Any, job: Any, expected: str | None) -> Any:
    prior = job.get("archive_deferral_confirmation")
    if prior and expected == prior:
        return job
    proof = preview(config, db, job)
    if expected != proof["plan_sha256"]:
        raise InvalidInput("maintenance_archive_deferral_confirmation_required")
    by_operation = {i["operation"]: i for i in proof["items"]}
    job["archive_deferral_previous_failure"] = {
        "state": job["state"],
        "error": job.get("error"),
        "error_detail": job.get("error_detail"),
        "recovery_error": job.get("recovery_error"),
    }
    for item in job["items"]:
        receipt = by_operation.get(item.get("archive_operation"))
        if receipt:
            item.update(
                state="waiting_archive",
                error=None,
                archive_deferral={
                    **receipt,
                    "confirmed_at": time.time(),
                    "previous_state": item["state"],
                    "previous_error": item.get("error"),
                },
                archive_wait={
                    "state": "blocked",
                    "reason": receipt["reason"],
                    "links": receipt["links"],
                },
            )
    job.update(
        state="queued",
        error=None,
        archive_deferral_confirmation=expected,
        generation=job.get("generation", 0) + 1,
    )
    job.pop("error_detail", None)
    job.pop("recovery_error", None)
    store.put(config, "jobs", job["id"], job["vault_id"], job)
    return job


def resume_check(db: Any, job: Any) -> None:
    """An unready immutable plan cannot become executable by requeueing it."""
    for item in job["items"]:
        if (
            not item.get("archive_operation")
            or item.get("archive_deferral")
            or item.get("phase") == "archived"
        ):
            continue
        row = db.execute(
            "SELECT state,payload FROM vault_operations WHERE id=? AND vault_id=?",
            (item["archive_operation"], job["vault_id"]),
        ).fetchone()
        if row and row["state"] != "completed":
            value = json.loads(row["payload"])
            expiry = value.get("expires_at") if isinstance(value, dict) else None
            if (
                not isinstance(value, dict)
                or expiry is not None
                and (not isinstance(expiry, (int, float)) or isinstance(expiry, bool))
            ):
                raise IntegrityError("maintenance_close_recovery_required")
            if value.get("unresolved_links") or (
                expiry is not None
                and time.time() > expiry
                and value.get("metadata_committed") is not True
            ):
                raise InvalidInput("maintenance_archive_deferral_review_required")
