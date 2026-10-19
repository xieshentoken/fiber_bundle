"""Only finite channel errors may cross the HTTP boundary."""

from fastapi.exceptions import RequestValidationError

from ..config import InvalidInput
from .validation import ChannelValidationError

CODES = frozenset(
    {
        "channel_runtime_manifest_invalid",
        "channel_runtime_manifest_unreadable",
        "channel_runtime_manifest_local_required",
        "channel_credential_invalid",
        "channel_credential_required",
        "channel_feishu_app_required",
        "channel_weixin_requires_qr_login",
        "channel_model_policy_unavailable",
        "channel_expert_requires_feishu_add_original_limit",
        "channel_expert_disabled_limits",
        "channel_explicit_budgets_required",
        "channel_pairing_required",
        "channel_pairing_candidate_changed",
        "channel_pause_required",
        "channel_pause_or_revision_required",
        "channel_instance_already_running",
        "channel_operation_pending",
        "channel_removed",
        "channel_launch_failed",
        "channel_delivery_requires_reconciliation",
        "channel_revision_conflict",
        "channel_process_not_stopped",
        "channel_cleanup_requires_review",
        "channel_legacy_process_unverified",
        "channel_restored_confirmation_required",
        "channel_legacy_start_unavailable",
        "channel_list_limit",
    }
)


def response(error: Exception) -> tuple[int, dict[str, object]] | None:
    if isinstance(error, RequestValidationError):
        fields = {
            "name",
            "app_id",
            "client_id",
            "vault_id",
            "runtime_manifest",
            "query_limit",
            "outbound_limit",
            "model_limit",
            "download_limit",
            "expert_limit",
            "expert_receipt_limit",
            "app_secret",
            "credential_file",
        }
        issues = list(
            dict.fromkeys(
                str(item["loc"][-1]) + "_invalid"
                for item in error.errors()
                if item.get("loc") and item["loc"][-1] in fields
            )
        )
        return 422, {"error": "channel_input_invalid", "issues": issues}
    if isinstance(error, ChannelValidationError):
        return error.status, {"error": str(error), "issues": error.issues}
    if isinstance(error, InvalidInput) and str(error) in CODES:
        return 422, {"error": str(error)}
    return None
