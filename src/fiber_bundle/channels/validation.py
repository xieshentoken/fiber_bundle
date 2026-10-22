"""Finite, non-sensitive channel configuration issues shared by save and start guards."""

from typing import Any

from .. import auth, vaults
from ..config import Config, InvalidInput

ISSUES = frozenset(
    {
        "expert_feishu_required",
        "expert_original_required",
        "expert_add_required",
        "expert_limit_required",
        "expert_limits_disabled",
        "query_limit_required",
        "outbound_limit_required",
        "model_limit_required",
        "query_permission_required",
        "original_permission_required",
        "add_permission_required",
        "vault_unavailable",
        "add_read_only",
        "client_unavailable",
    }
)


class ChannelValidationError(InvalidInput):
    def __init__(self, issues: list[str], *, status: int = 422):
        if not issues or any(issue not in ISSUES for issue in issues):
            raise ValueError("unrecognized channel issue")
        self.issues = list(dict.fromkeys(issues))
        self.status = status
        super().__init__("channel_configuration_invalid")


def configuration_issues(value: dict[str, Any]) -> list[str]:
    issues = []
    if value.get("expert_enabled"):
        for condition, issue in (
            (value.get("platform") == "feishu", "expert_feishu_required"),
            (value.get("original"), "expert_original_required"),
            (value.get("add"), "expert_add_required"),
            (
                value.get("policy_version", 2) == 2 or value.get("expert_limit", 0) > 0,
                "expert_limit_required",
            ),
        ):
            if not condition:
                issues.append(issue)
    elif value.get("expert_limit") or value.get("expert_receipt_limit"):
        issues.append("expert_limits_disabled")
    return issues


def start_issues(value: dict[str, Any], *, intake_enabled: bool) -> list[str]:
    issues = configuration_issues(value)
    if value.get("policy_version", 2) != 2 and not (
        value.get("add") and (intake_enabled or value.get("expert_enabled"))
    ):
        if not value.get("query_limit"):
            issues.append("query_limit_required")
        if not value.get("outbound_limit"):
            issues.append("outbound_limit_required")
    if (
        value.get("answer_mode") != "evidence"
        and value.get("query_limit")
        and value.get("policy_version", 2) != 2
        and not value.get("model_limit")
    ):
        issues.append("model_limit_required")
    return issues


def require_permissions(config: Config, main: Any, channel: dict[str, Any]) -> None:
    value = channel["value"]
    if "query_vaults" in value:
        from . import query_scope

        query_scope.entries(value)
        client = main.execute(
            "SELECT active FROM clients WHERE id=?", (value["client_id"],)
        ).fetchone()
        if client is None or not client["active"]:
            raise ChannelValidationError(["client_unavailable"], status=403)
        # Scope permissions are checked for the selected message, not an unrelated library.
        if "_query_scope" in channel:
            query_scope.checked(config, main, channel)
        return
    issues = []
    for action in ("query", "original", "add"):
        if action != "query" and not value.get(action):
            continue
        try:
            vault = auth.grant(main, value["client_id"], value["vault_id"], action)
            from ..vault_relocation import guard

            if not (
                guard.metadata_only(value["vault_id"]) and guard.operation(main, value["vault_id"])
            ):
                vaults.checked_vault(config, vault)
        except auth.Denied:
            issues.append(action + "_permission_required")
        except InvalidInput:
            issues.append("vault_unavailable")
    if issues:
        raise ChannelValidationError(issues, status=403)
