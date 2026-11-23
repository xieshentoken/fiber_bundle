"""Explicit verification of committed archives, without resuming a maintenance task."""

import copy
import time
from typing import Any
from uuid import uuid4

from .. import cloud_observation, committed_namespace_addition, placement_committed
from ..cloud_helper_session import reuse as metadata_session
from ..config import IntegrityError, InvalidInput
from ..inputs import canonical
from ..note_read_session import reuse
from ..objects import digest
from ..vault_sync import dependencies
from ..vault_sync import store as index_store
from . import execution_control, packages, store
from . import maintenance_archive_finalization as finalization
from .maintenance_archive_evidence import task_binding

QUIESCENT = {"partial", "failed", "blocked", "paused", "waiting_file", "closed", "completed"}


def projection(config: Any, db: Any, job: Any, *, supplemental_proof: Any = None) -> dict[str, Any]:
    """Read-only action scope. The receipt is separate from the original failure."""
    result: dict[str, Any] = {
        "available": False,
        "operations": [],
        "receipts": job.get("archive_verifications", []),
    }
    if any(r["state"] in {"verifying", "unknown"} for r in result["receipts"]):
        result["conflict"] = "maintenance_verification_outcome_unknown"
        return result
    if not job.get("confirmed") or job["state"] not in QUIESCENT:
        return result
    try:
        operations = finalization.owned_operations(db, job)
        selected = [
            (row, value, item)
            for row, value, item in operations
            if row["state"] in {"applying", "reconcile"} and value.get("metadata_committed")
        ]
        if not selected:
            return result
        if supplemental_proof is not None:
            committed_namespace_addition.validate_shape(supplemental_proof)
            if len(selected) != 1 or selected[0][0]["id"] != supplemental_proof["operation_id"]:
                raise IntegrityError("placement_namespace_addition_conflict")
        result.update(
            available=True,
            operations=[
                {"id": row["id"], "state": row["state"], "entity": item["entity"]}
                for row, _, item in selected
            ],
            plan_sha256=digest(
                canonical(
                    {
                        "job": task_binding(job),
                        **(
                            {"supplemental_proof": supplemental_proof}
                            if supplemental_proof is not None
                            else {}
                        ),
                        "operations": [
                            [row["id"], row["state"], digest(row["payload"].encode())]
                            for row, _, _ in selected
                        ],
                    }
                ).encode()
            ),
        )
    except IntegrityError as error:
        result["conflict"] = str(error)
    return result


@reuse()
@metadata_session()
def apply(
    config: Any, db: Any, job: Any, expected: str | None, *, supplemental_proof: Any = None
) -> Any:
    from . import (
        maintenance,
        maintenance_apply,
        maintenance_membership_validation,
        maintenance_undo,
    )
    from .maintenance_archive_evidence import journal_proof

    # Permission is rechecked even for a repeated receipt read.
    packages.context(config, db, job["client_id"], job["vault_id"], write=True)
    with execution_control.claim(config, job["id"]) as acquired:
        if not acquired:
            raise InvalidInput("organization_execution_pause_required")
        if store.get(config, "jobs", job["id"]) != job:
            raise IntegrityError("maintenance_verification_state_changed")
        if expected:
            receipt = next(
                (
                    r
                    for r in job.get("archive_verifications", [])
                    if r["request_sha256"] == expected
                ),
                None,
            )
            if receipt:
                if canonical(receipt.get("supplemental_proof")) != canonical(supplemental_proof):
                    raise IntegrityError("maintenance_verification_state_changed")
                return job
        scope = projection(config, db, job, supplemental_proof=supplemental_proof)
        if not scope["available"]:
            raise InvalidInput(scope.get("conflict", "maintenance_committed_archive_required"))
        if expected != scope["plan_sha256"]:
            raise IntegrityError("maintenance_verification_state_changed")
        maintenance.validate(config, db, job, sources=False)
        selected = {entry["id"] for entry in scope["operations"]}
        for other in store.rows(config, "jobs", job["vault_id"]):
            if other["id"] != job["id"] and any(
                item.get("archive_operation") in selected for item in other.get("items", [])
            ):
                raise IntegrityError("maintenance_archive_owner_conflict")
        selected_items = [
            item for row, _, item in finalization.owned_operations(db, job) if row["id"] in selected
        ]
        if any(
            item.get("phase") not in {"members_done", "archived"}
            or item.get("intent")
            or item.get("archive_deferral")
            for item in selected_items
        ):
            raise IntegrityError("maintenance_archive_binding_changed")
        # A durable intent prevents an unknown HTTP/crash outcome from becoming an
        # automatic second execution. Refreshing the task always exposes this receipt.
        receipt = {
            "id": str(uuid4()),
            "request_sha256": expected,
            "state": "verifying",
            "operations": sorted(selected),
            "created_at": time.time(),
            "model_calls": 0,
            "task_resumed": False,
        }
        if supplemental_proof is not None:
            receipt["supplemental_proof"] = copy.deepcopy(supplemental_proof)
        job.setdefault("archive_verifications", []).append(receipt)
        store.put(config, "jobs", job["id"], job["vault_id"], job)
        working = copy.deepcopy(job)
        try:
            # Older installations may still hold a generic failed-index cooldown.
            # Bind that wait before the original operation is released, without
            # submitting/building index work or creating an absent observer ledger.
            if index_store.library(config, job["vault_id"]) is not None:
                dependencies.refresh(config, job["vault_id"])
            finalization.resume(
                config,
                db,
                working,
                operation_ids=selected,
                **(
                    {"namespace_addition": supplemental_proof}
                    if supplemental_proof is not None
                    else {}
                ),
            )
            root, allowed = packages.context(
                config, db, job["client_id"], job["vault_id"], write=True
            )
            for row, value, item in finalization.owned_operations(db, working):
                if row["id"] not in selected:
                    continue
                if row["state"] != "completed" or not value.get("metadata_committed"):
                    raise IntegrityError("placement_committed_evidence_missing")
                placement_committed.verify(
                    config,
                    db,
                    value,
                    root,
                    allowed,
                    **(
                        {"namespace_addition": supplemental_proof}
                        if supplemental_proof is not None
                        else {}
                    ),
                )
                maintenance_apply.validate_decisions(config, working, item)
                maintenance_membership_validation.validate_item(
                    config, db, working, item, complete=True
                )
            # The proof rejects any pending exchange before seal can reconcile it.
            # No unrelated item or pending write is advanced by this action.
            journal_proof(config, db, working)
            maintenance_undo.seal(config, db, working)
            for item in working["items"]:
                if item.get("archive_operation") in selected:
                    item.update(
                        state="done",
                        phase="archived",
                        changed=len(item["add"]) + len(item["remove"]),
                    )
            from .manual_resolution import resolved

            handled = resolved(config, working)
            working["completed"] = sum(
                item["state"] == "done" or item["entity"] in handled for item in working["items"]
            )
            # Keep state, error, item errors and all original history intact.
            if index_store.library(config, job["vault_id"]) is not None:
                dependencies.refresh(config, job["vault_id"])
            receipt = working["archive_verifications"][-1]
            receipt.update(state="verified", verified_at=time.time())
            job = working
        except Exception as error:
            # Original operation journals retain exact failure evidence; a conflict
            # receipt never claims the task/item completed or queues another unit.
            receipt.update(
                state="conflict"
                if isinstance(error, (IntegrityError, InvalidInput))
                else "unknown",
                error=str(error)
                if isinstance(error, (IntegrityError, InvalidInput))
                else type(error).__name__,
                checked_at=time.time(),
            )
            if isinstance(error, cloud_observation.ObservationError):
                receipt["detail"] = error.detail
            try:
                if index_store.library(config, job["vault_id"]) is not None:
                    dependencies.refresh(config, job["vault_id"])
            except Exception as wake_error:
                receipt["index_wait_error"] = type(wake_error).__name__
        store.put(config, "jobs", job["id"], job["vault_id"], job)
        return job
