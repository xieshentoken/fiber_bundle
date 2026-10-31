"""Bounded read/preview diagnostics; never publish arbitrary storage exception text."""

from typing import Any

from ..config import IntegrityError, InvalidInput
from .catalog_inventory import Unavailable


def response(error: Exception) -> tuple[int, dict[str, Any]] | None:
    if isinstance(error, Unavailable):
        return 409, {"error": str(error), "issues": error.issues}
    if isinstance(error, (IntegrityError, InvalidInput)) and str(error) in {
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
