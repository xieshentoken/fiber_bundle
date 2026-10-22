"""Validated GUI configuration; secrets are private and never included in public views."""

import json
import re
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field

from .. import feishu_gateway, feishu_gateway_cli, query_policy_store
from ..config import Config, InvalidInput, contained, make_directory
from ..payloads import Identifier, Payload
from ..query_http import Digest
from ..query_routing_files import read
from . import query_scope, store


class Settings(Payload):
    policy_version: Literal[1, 2] = 1
    name: Annotated[str, Field(min_length=1, max_length=100)]
    purpose: Literal["query", "intake"] | None = None
    platform: Literal["feishu", "weixin"]
    app_id: Annotated[str, Field(pattern=r"^cli_[A-Za-z0-9]+$")] | None = None
    client_id: Identifier
    vault_id: Identifier
    query_vaults: (
        Annotated[list[query_scope.QueryVault], Field(min_length=1, max_length=20)] | None
    ) = None
    expert_enabled: bool = False
    expert_media: Literal["unknown", "text", "images", "full"] = "unknown"
    expert_limit: Annotated[int, Field(strict=True, ge=0, le=100)] = 0
    expert_receipt_limit: Annotated[int, Field(strict=True, ge=0, le=100)] = 0
    original: bool = False
    add: bool = False
    answer_mode: Literal["evidence", "local", "local_cloud", "model"] = "evidence"
    natural_questions: bool = False
    configuration_sha256: Digest | None = None
    query_limit: Annotated[int, Field(strict=True, ge=0, le=100)] = 0
    model_limit: Annotated[int, Field(strict=True, ge=0, le=100)] = 0
    outbound_limit: Annotated[int, Field(strict=True, ge=0, le=100)] = 0
    download_limit: Annotated[int, Field(strict=True, ge=0, le=100)] = 0
    runtime_manifest: Annotated[str, Field(min_length=1, max_length=4096)]


class Save(Payload):
    id: Identifier | None = None
    expected_revision: Annotated[int, Field(strict=True, ge=1)] | None = None
    settings: Settings
    credential_file: Annotated[str, Field(max_length=4096)] | None = None
    app_secret: Annotated[str, Field(max_length=4096)] | None = None


def secret_path(config: Config, identity: str) -> Path:
    return contained(config.state_root, "message-channel-secrets/" + identity + ".json")


def feishu_settings(config: Config, channel: dict[str, Any]) -> feishu_gateway.Settings:
    value = channel["value"]
    return feishu_gateway.Settings(
        app_id=value["app_id"],
        owner_id=channel["owner"] or "ou_unpaired",
        client_id=value["client_id"],
        vault_id=value["vault_id"],
        home="configs/hermes-feishu-gui-" + channel["id"],
        secret_file=str(secret_path(config, channel["id"])),
    )


_RUNTIME_KEYS = {"source", "dependencies", "sha256", "scanner", "scanner_sha256"}


def require_runtime_manifest(config: Config, manifest: object) -> None:
    """Accept the legacy manifest and the component-pinned manifest.

    The installed Hermes component records a ``component`` pin beside the old
    fields. Saving must keep that integrity check instead of rejecting the pin.
    """
    if not isinstance(manifest, dict) or set(manifest) - {"component"} != _RUNTIME_KEYS:
        raise InvalidInput("channel_runtime_manifest_invalid")
    if "component" not in manifest:
        return
    selection = manifest["component"]
    if (
        not isinstance(selection, dict)
        or set(selection) != {"root", "sha256"}
        or not isinstance(selection.get("root"), str)
        or not isinstance(selection.get("sha256"), str)
    ):
        raise InvalidInput("channel_runtime_manifest_invalid")
    try:
        feishu_gateway_cli.verify_component(config, manifest)
    except InvalidInput:
        raise InvalidInput("channel_runtime_manifest_invalid") from None
    except (OSError, TypeError, ValueError, KeyError):
        raise InvalidInput("channel_runtime_manifest_invalid") from None


def save(config: Config, main: Any, data: Save) -> dict[str, Any]:
    value = data.settings.model_dump()
    if "query_vaults" not in data.settings.model_fields_set:
        value.pop("query_vaults")
        if data.id:
            with store.readonly(config) as prior_db:
                assert prior_db is not None
                prior_scope = store.get(prior_db, data.id)["value"].get("query_vaults")
            if prior_scope is not None:
                value["query_vaults"] = prior_scope
    if value.get("query_vaults") is None:
        value.pop("query_vaults", None)
    selected_vaults = query_scope.entries(value)
    if "query_vaults" in value:
        value["vault_id"] = selected_vaults[0]["vault_id"]
    if value["purpose"] is None:
        value.pop("purpose")  # Existing mixed-purpose entries keep their behavior and identity.
    elif value["purpose"] == "query" and value["add"]:
        raise InvalidInput("channel_query_cannot_capture_materials")
    elif value["purpose"] == "intake" and (not value["add"] or value["platform"] != "feishu"):
        raise InvalidInput("channel_intake_requires_feishu_add")
    if "natural_questions" not in data.settings.model_fields_set:
        value.pop("natural_questions")
        if data.id:
            with store.opened(config) as side:
                prior = store.get(side, data.id)["value"]
            if "natural_questions" in prior:
                value["natural_questions"] = prior["natural_questions"]
    # Do not backfill a new default into old channel values or their fingerprints.
    if "expert_media" not in data.settings.model_fields_set:
        value.pop("expert_media")
        if data.id:
            with store.opened(config) as side:
                prior_media = store.get(side, data.id)["value"].get("expert_media")
            if prior_media is not None:
                value["expert_media"] = prior_media
    from .validation import ChannelValidationError, configuration_issues

    issues = configuration_issues(value)
    if issues:
        raise ChannelValidationError(issues)
    if value["platform"] == "feishu" and not value["app_id"]:
        raise InvalidInput("channel_feishu_app_required")
    if value["platform"] == "weixin" and (
        data.app_secret or data.credential_file or value["app_id"]
    ):
        raise InvalidInput("channel_weixin_requires_qr_login")
    path = Path(value["runtime_manifest"])
    if not path.is_absolute() or not path.is_relative_to(config.project_root):
        raise InvalidInput("channel_runtime_manifest_local_required")
    # Bounded fixed manifest only; no runtime is imported in the GUI/backend process.
    try:
        manifest = json.loads(read(path, 65536))
    except (OSError, InvalidInput):
        raise InvalidInput("channel_runtime_manifest_unreadable") from None
    except ValueError:
        raise InvalidInput("channel_runtime_manifest_invalid") from None
    require_runtime_manifest(config, manifest)
    secret = None
    if data.app_secret:
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,4096}", data.app_secret):
            raise InvalidInput("channel_credential_invalid")
        secret = data.app_secret
    elif data.credential_file:
        settings = feishu_gateway.Settings(
            app_id=value["app_id"],
            owner_id="ou_unpaired",
            client_id=value["client_id"],
            vault_id=value["vault_id"],
            home="configs/hermes-feishu-gui-preview",
            secret_file=data.credential_file,
        )
        secret = feishu_gateway_cli.load_secret(settings)
    if value["platform"] == "feishu" and secret is None:
        if not data.id or not secret_path(config, data.id).exists():
            raise InvalidInput("channel_credential_required")
        with store.opened(config) as db:
            prior = store.get(db, data.id)
        if prior["value"]["app_id"] != value["app_id"]:
            raise InvalidInput("channel_credential_required")
    channel = store.save(config, main, value, data.id, data.expected_revision)
    if secret is not None:
        target = secret_path(config, channel["id"])
        make_directory(target.parent)
        query_policy_store.write_private(
            target, json.dumps({"app_id": value["app_id"], "app_secret": secret}).encode()
        )
    return public(config, channel)


def public(config: Config, channel: dict[str, Any]) -> dict[str, Any]:
    from .control import status

    check = None
    path = store.home(config, channel["id"]) / "check-result.json"
    if path.exists():
        try:
            value = json.loads(read(path, 4096))
            check = {
                key: value[key]
                for key in ("check", "error", "permissions", "network", "checked_at", "diagnostic")
                if key in value
            }
        except (OSError, ValueError, TypeError, InvalidInput):
            check = {"check": "unavailable", "error": "channel_check_result_invalid"}

    return {
        "id": channel["id"],
        "revision": channel["revision"],
        "state": channel["state"],
        "settings": channel["value"],
        "owner_id": channel["owner"],
        "candidate_owner": (channel["candidate"] or {}).get("owner_id"),
        "challenge": channel["challenge"],
        "credential_saved": secret_path(config, channel["id"]).is_file(),
        "runtime": status(config, channel["id"]),
        "connection_check": check,
    }
