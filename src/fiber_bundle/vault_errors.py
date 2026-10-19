"""Public catalog diagnostics are finite codes, never filesystem exception text."""

from .config import InvalidInput, StorageError

CODES = frozenset(
    {
        "vault_operation_requires_reconciliation",
        "vault_read_only",
        "vault_index_requires_query_identity",
        "cloud_item_not_downloaded",
        "cloud_item_pending_stale_or_conflicted",
        "cloud_status_helper_required",
        "cloud_status_helper_changed",
        "cloud_symlink_not_allowed",
        "vault_file_alias",
        "vault_file_size_limit",
        "vault_note_limit",
        "vault_reference_limit",
        "vault_reference_invalid",
        "vault_source_changed",
        "vault_preview_changed",
        "vault_preview_expired",
        "vault_trash_helper_required",
        "vault_trash_helper_changed",
        "vault_trash_macos_required",
        "vault_trash_protected_or_partial_scope",
        "vault_trash_result_invalid",
        "vault_trash_result_ambiguous",
        "vault_policy_invalid",
        "vault_file_limit_or_alias",
    }
)


def response(error: Exception) -> tuple[int, dict[str, object]] | None:
    if isinstance(error, (InvalidInput, StorageError)) and str(error) in CODES:
        return 422, {"error": str(error)}
    if (
        isinstance(error, (InvalidInput, StorageError))
        and str(error) == "vault directory identity changed"
    ):
        return 409, {"error": "vault_directory_changed"}
    return None
