"""Bounded read/preview diagnostics; never publish arbitrary storage exception text."""

from typing import Any

from ..config import IntegrityError, InvalidInput
from .catalog_inventory import Unavailable


def response(error: Exception) -> tuple[int, dict[str, Any]] | None:
    if (
        isinstance(error, (IntegrityError, InvalidInput))
        and str(error) == "moc_retained_member_limit"
    ):
        from ..cloud_observation import ObservationError
        from .observation_diagnostic_http import bounded_detail

        body: dict[str, Any] = {"error": str(error)}
        if isinstance(error, ObservationError):
            body["error_detail"] = bounded_detail(error)
        return 503, body
    if isinstance(error, Unavailable):
        return 409, {"error": str(error), "issues": error.issues}
    if isinstance(error, InvalidInput) and str(error) == "maintenance_close_confirmation_required":
        return 422, {"error": str(error)}
    if isinstance(error, (IntegrityError, InvalidInput)) and str(error) in {
        "organization_execution_pause_required",
        "maintenance_verification_state_changed",
        "maintenance_verification_outcome_unknown",
        "maintenance_committed_archive_required",
        "maintenance_archive_owner_conflict",
        "maintenance_undo_file_conflict",
        "maintenance_archive_deferral_review_required",
        "maintenance_archive_deferral_required",
        "maintenance_archive_deferral_confirmation_required",
        "maintenance_close_pause_required",
        "maintenance_close_recovery_required",
        "maintenance_close_confirmation_required",
        "maintenance_archive_binding_changed",
        "maintenance_archive_scope_changed",
        "maintenance_archive_target_conflict",
        "maintenance_note_changed",
        "maintenance_membership_plan_changed",
        "maintenance_membership_baseline_changed",
        "partial_note_scope_changed",
        "partial_note_scope_invalid",
        "partial_note_scope_truncated",
        "cloud_recovery_file_changed",
        "cloud_recovery_status_timeout",
        "cloud_recovery_download_unknown",
        "cloud_recovery_status_unavailable",
        "cloud_recovery_macos_required",
        "cloud_status_helper_required",
        "maintenance_link_repair_none_pending",
        "maintenance_link_repair_task_not_ready",
        "maintenance_link_repair_preview_changed",
        "maintenance_link_repair_source_changed",
        "maintenance_link_repair_destination_changed",
        "maintenance_link_repair_old_path_reused",
        "maintenance_link_repair_mapping_ambiguous",
        "maintenance_link_repair_source_ambiguous",
        "maintenance_link_repair_scope_changed",
        "maintenance_link_repair_not_ready",
        "maintenance_undo_link_repair_completed",
        "cloud_item_not_downloaded",
        "cloud_metadata_unavailable",
        "cloud_item_changed",
    }:
        return 409, {"error": str(error)}
    return None
