"""Read-only channel overview: process evidence, transport and capabilities are distinct."""

import hashlib
import sqlite3
import time
from typing import Any

from .. import auth, feishu_runtime, query_policy_store
from ..config import Config, InvalidInput
from ..inputs import canonical
from . import configuration, control, intake_repository, legacy, operations, store


def fingerprint(channel: dict[str, Any]) -> str:
    return (
        channel.get("configuration_sha256")
        or hashlib.sha256(
            canonical(
                {
                    "value": channel["value"],
                    "owner": channel["owner"],
                    "revision": channel["revision"],
                }
            ).encode()
        ).hexdigest()
    )


def transport(runtime: dict[str, Any]) -> str:
    if runtime["process_state"] == "stopped":
        return "disconnected"
    if not runtime["heartbeat_fresh"] or runtime.get("reason") == "instance_mismatch":
        return "unknown"
    state = runtime.get("transport_state", runtime["state"])
    if state in {"connected", "query_ready"}:
        return "connected"
    if state in {"starting", "checking", "reconnecting"}:
        return "connecting"
    if state in {"waiting_for_login", "waiting_for_pairing", "candidate_received"}:
        return "awaiting_binding"
    return "disconnected" if state in {"disconnected", "error", "failed", "stopped"} else "unknown"


def capability(enabled: bool, ready: bool, reason: str) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "available": enabled and ready,
        "reason": None if enabled and ready else reason,
    }


def present(config: Config, main: sqlite3.Connection, channel: dict[str, Any]) -> dict[str, Any]:
    old = channel.get("origin") == "legacy"
    value = channel["value"]
    platform = "feishu" if old else value["platform"]
    home = legacy.home(config, channel) if old else store.home(config, channel["id"])
    runtime = feishu_runtime.inspect(home) if old else control.status(config, channel["id"])
    runtime["platform"] = platform
    with store.readonly(config) as side:
        operation = operations.public(operations.latest(side, channel["id"]))
        intake = intake_repository.present(side, channel)
    state = channel["state"]
    permissions: dict[str, bool] = {}
    for action in ("query", "original", "add"):
        try:
            auth.grant(main, value["client_id"], value["vault_id"], action)
            permissions[action] = True
        except auth.Denied:
            permissions[action] = False
    used: dict[str, int] = {"query": 0, "model": 0, "outbound": 0}
    last_received = None
    unresolved = 0
    if not old:
        with store.readonly(config) as db:
            assert db is not None
            row = db.execute(
                "SELECT count(*),coalesce(sum(json_extract(value,'$.model_calls_reserved')),0),"
                "sum(state IN ('sending','sent','unknown')),max(created) "
                "FROM events WHERE channel_id=? AND revision=?",
                (channel["id"], channel["revision"]),
            ).fetchone()
            used = dict(
                zip(("query", "model", "outbound"), (int(n or 0) for n in row[:3]), strict=True)
            )
            last_received = row[3]
            unresolved = db.execute(
                "SELECT count(*) FROM events WHERE channel_id=? AND state IN ('sending','unknown')",
                (channel["id"],),
            ).fetchone()[0]
    shared_model_budget = not old and value.get("policy_version", 2) == 2
    limits: dict[str, int | None] = {
        key: None
        if shared_model_budget
        else max(0, int(runtime.get(key + "_remaining", 0)))
        if old
        else max(0, value[key + "_limit"] - used[key])
        for key in ("query", "model", "outbound")
    }
    if old:
        limits["query"] = limits["model"]
    bound = bool(channel["owner"] and channel["owner"] != "ou_unpaired")
    ready = (
        state == "running"
        and bound
        and runtime["available"]
        and not unresolved
        and (old or runtime.get("revision") == channel["revision"])
    )
    query_enabled = value.get("query_enabled", False) if old else True
    selected_model = query_enabled if old else value["answer_mode"] != "evidence"
    policy_ready = True
    if not old and selected_model:
        try:
            query_policy_store.checked(config, value["configuration_sha256"])
        except (OSError, ValueError, InvalidInput):
            policy_ready = False
    query_ready = (
        ready
        and permissions["query"]
        and query_enabled
        and (limits["query"] is None or limits["query"] > 0)
        and (limits["outbound"] is None or limits["outbound"] > 0)
        and (
            not selected_model
            or ((limits["model"] is None or limits["model"] > 0) and policy_ready)
        )
    )
    reason = (
        "binding_required"
        if not bound
        else "delivery_requires_reconciliation"
        if unresolved
        else "permission_revoked"
        if not permissions["query"]
        else "policy_changed"
        if not policy_ready
        else "channel_paused"
        if state != "running"
        else "budget_or_switch_disabled"
        if not query_enabled
        or limits["query"] == 0
        or limits["outbound"] == 0
        or (selected_model and not shared_model_budget and not limits["model"])
        else "runtime_unavailable"
    )
    add = True if old else value["add"]
    downloading = runtime.get("download_remaining", 0) > 0
    capture_ready = ready and permissions["add"] and add
    binding_state = "bound" if bound else "unpaired"
    if state in {"pair", "login"}:
        binding_state = "awaiting_pair" if state == "pair" else "awaiting_scan"
    if channel.get("candidate"):
        binding_state = (
            "awaiting_confirmation"
            if channel["candidate"].get("expires_at", 0) > time.time()
            else "candidate_expired"
        )
    if not old and platform == "weixin" and bound and not (home / "weixin-login.json").is_file():
        binding_state, query_ready, capture_ready = "login_required", False, False
        reason = "login_required"
    legacy_configured = not old or (home / "receiver.json").is_file()
    if not legacy_configured:
        binding_state, query_ready, capture_ready = "configuration_required", False, False
    result = {
        "id": channel["id"],
        "origin": "legacy" if old else "gui",
        "revision": channel["revision"],
        "configuration_sha256": fingerprint(channel),
        "state": state,
        "name": "既有飞书 · " + home.name if old else value["name"],
        "platform": platform,
        "app_id": value.get("app_id"),
        "owner_id": channel["owner"] if bound else None,
        "vault_id": value["vault_id"],
        "binding_state": binding_state,
        "runtime": runtime,
        "transport": transport(runtime),
        "remaining": limits,
        "capabilities": {
            "query": capability(query_enabled, query_ready, reason),
            "model": capability(selected_model, query_ready, reason),
            "capture": capability(add, capture_ready, "permission_or_runtime_unavailable"),
            "download": capability(
                add and downloading and platform == "feishu",
                capture_ready,
                "download_budget_or_runtime_unavailable",
            ),
            "store": capability(
                old and value.get("store_limit", 0) > 0,
                capture_ready and runtime.get("store_remaining", 0) > 0,
                "manual_confirmation_or_budget_required",
            ),
        },
        "unresolved_deliveries": unresolved,
        "last_received_at": last_received,
        "end_to_end_verified_at": None,
        "observed_at": time.time(),
        "start_supported": legacy_configured and (not old or bool(channel.get("runtime_manifest"))),
        "configuration": None if old else configuration.public(config, channel),
        "operation": operation,
    }
    from .services.projection import project as service_projection

    result["services"] = service_projection(config, channel)
    service_roles = result["services"]["roles"]
    query_service = service_roles.get("query")
    if query_service is None or query_service["managed"] and not query_service["enabled"]:
        for key in ("query", "model"):
            result["capabilities"][key] = capability(False, False, "channel_query_service_paused")
    elif query_service["managed"] and not (
        query_service.get("process_running") and query_service.get("heartbeat_fresh")
    ):
        for key in ("query", "model"):
            result["capabilities"][key]["available"] = False
            result["capabilities"][key]["reason"] = "channel_service_not_running"
    intake_service = service_roles.get("intake")
    independent_intake = bool(intake_service and intake_service["managed"])
    processing_ready = (
        (
            state in {"running", "paused", "stopping", "error"}
            and bound
            and permissions["add"]
            and add
        )
        if independent_intake
        else capture_ready
    )
    if intake["revision"]:
        target = main.execute(
            "SELECT name FROM vaults WHERE id=?", (intake["target_vault_id"],)
        ).fetchone()
        blocked = None
        try:
            intake_repository.authorize(config, main, channel, intake["settings"])
            from .intake_health import admission_reason

            blocked = admission_reason(config, main, channel, intake["settings"])
        except auth.Denied:
            blocked = "intake_permission_unavailable"
        except InvalidInput as error:
            blocked = (
                str(error) if str(error).startswith("intake_") else "intake_target_unavailable"
            )
        blocked = (
            blocked
            or ("intake_binding_changed" if not intake["binding_current"] else None)
            or (
                "intake_channel_paused"
                if not processing_ready
                or operation
                and operation["state"] in {"pending", "needs_review"}
                else None
            )
            or ("intake_store_budget_exhausted" if intake["remaining"]["store"] == 0 else None)
        )
        enabled = intake["settings"]["enabled"] and (
            not independent_intake or bool(intake_service["enabled"])
        )
        from .batch_intake import worker_status
        from .intake_health import pending_review

        worker = worker_status(config, channel)
        if independent_intake:
            worker = {
                "state": "off"
                if not intake_service["enabled"]
                else "needs_review"
                if intake_service["state"] == "blocked"
                else "processing"
                if intake_service["state"] == "working"
                else "ready"
                if intake_service.get("process_running")
                else "unobserved",
                "fresh": intake_service.get("heartbeat_fresh", False),
                "error": intake_service.get("error"),
                "observed_at": None,
            }
        review_count = pending_review(config, channel["id"])
        blocked = blocked or (
            "intake_worker_stopped"
            if enabled and worker["state"] == "needs_review"
            else "intake_storage_busy"
            if enabled and worker["state"] == "waiting_storage"
            else "intake_worker_unobserved"
            if enabled
            and (not worker["fresh"] or worker["state"] in {"unobserved", "off", "unconfigured"})
            else None
        )
        result["capabilities"]["store"] = capability(
            enabled, blocked is None, blocked or "intake_switch_off"
        )
        result["capabilities"]["download"] = capability(
            enabled
            and (
                intake["settings"].get("policy_version", 2) == 2
                or intake["settings"]["download_limit"] > 0
            ),
            blocked is None and intake["remaining"]["download"] != 0,
            blocked or "intake_download_budget_exhausted",
        )
        result["intake"] = {
            "enabled": enabled,
            "target_name": target[0] if target else None,
            "reason": blocked or ("intake_requires_review" if review_count else None),
            "worker": worker,
            "pending_review": review_count,
            "used": intake["used"],
            "revision": intake["revision"],
        }
    return result


def overview(config: Config, main: sqlite3.Connection) -> dict[str, Any]:
    channels, issues = legacy.entries(config)
    with store.readonly(config) as db:
        channels = operations.legacy_overlay(db, channels)
        if db is not None:
            for is_removed in (False, True):
                for row in db.execute(
                    "SELECT id FROM channels WHERE (state='removed')=? ORDER BY rowid LIMIT 101",
                    (is_removed,),
                ):
                    channels.append(store.get(db, row[0]))
    active = [channel for channel in channels if channel["state"] != "removed"]
    removed = [channel for channel in channels if channel["state"] == "removed"]
    if len(active) > 100 or len(removed) > 100:
        issues.append({"reason": "channel_list_limit"})
    result = [present(config, main, channel) for channel in active[:100] + removed[:100]]
    return {
        "schema": "channel-overview/v1",
        "observed_at": time.time(),
        "channels": [entry for entry in result if entry["state"] != "removed"],
        "removed_channels": [
            {key: entry[key] for key in ("id", "name", "platform", "origin", "operation")}
            for entry in result
            if entry["state"] == "removed"
        ],
        "issues": issues,
    }
