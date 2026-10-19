"""Pinned Hermes Feishu adapter with owner-only dispatch and optional archival intake."""

import asyncio
import importlib
import json
from typing import Any
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .. import feishu_delivery, feishu_gateway, feishu_gateway_cli, feishu_pairing, feishu_receiver
from ..config import Config, InvalidInput
from . import configuration, dispatch, store


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        raise InvalidInput("channel_credentials_redirect_rejected")


def validate_credentials(config: Config, channel: dict[str, Any]) -> dict[str, Any]:
    settings = configuration.feishu_settings(config, channel)
    secret = feishu_gateway_cli.load_secret(settings)
    request = Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=json.dumps({"app_id": settings.app_id, "app_secret": secret}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # Credential validation only, no messaging/model endpoint and no retries.
    with build_opener(NoRedirect()).open(request, timeout=15) as response:
        body = response.read(65537)
    if len(body) > 65536 or json.loads(body).get("code") != 0:
        raise InvalidInput("channel_credentials_rejected")
    return {
        "check": "credentials_validated",
        "permissions": "请在平台核对消息事件、机器人能力与发布状态",
    }


def adapter(config: Config, channel: dict[str, Any]) -> Any:
    upstream = importlib.import_module("plugins.platforms.feishu.adapter")
    settings = configuration.feishu_settings(config, channel)
    base = feishu_gateway.adapter_class(upstream.FeishuAdapter, upstream.lark, config, settings)
    pc_type = importlib.import_module("gateway.config").PlatformConfig

    class Adapter(base):  # type: ignore[misc,valid-type]
        def _on_message_event(self, event: Any) -> None:
            try:
                payload = json.loads(upstream.lark.JSON.marshal(event))
                message = payload.get("event", {}).get("message", {})
                sender = payload.get("event", {}).get("sender", {})
                if (
                    payload.get("header", {}).get("app_id") != settings.app_id
                    or message.get("chat_type") != "p2p"
                    or sender.get("sender_type") != "user"
                    or sender.get("sender_id", {}).get("open_id") != settings.owner_id
                ):
                    return
                body = json.loads(message.get("content", "{}"))
                if message.get("message_type") == "text":
                    if str(body.get("text", "")).startswith("/归类") and channel["value"]["add"]:
                        feishu_receiver.capture(
                            config,
                            settings.binding,
                            payload,
                            commands=True,
                            guard=lambda: dispatch.archival_guard(
                                config, channel["id"], channel["revision"]
                            ),
                        )
                        return
                    from .query import question

                    if (
                        question(
                            body.get("text", ""), channel["value"].get("natural_questions", False)
                        )
                        is not None
                    ):
                        dispatch.capture(
                            config,
                            channel["id"],
                            channel["revision"],
                            message["message_id"],
                            settings.owner_id,
                            body["text"],
                        )
                        return
                if channel["value"]["add"]:
                    from .expert import authorization

                    feishu_receiver.capture(
                        config,
                        settings.binding,
                        payload,
                        expert_authorization=authorization(channel["id"], channel["revision"])
                        if channel["value"].get("expert_enabled")
                        else None,
                        expert_limit=channel["value"].get("expert_limit", 0),
                        expert_unlimited=channel["value"].get("policy_version") == 2,
                        guard=lambda: dispatch.archival_guard(
                            config, channel["id"], channel["revision"]
                        ),
                    )
            except Exception:
                self._fiber_error = "channel_capture_failed"

    return Adapter(
        pc_type(
            enabled=True,
            gateway_restart_notification=False,
            extra={
                "app_id": settings.app_id,
                "app_secret": feishu_gateway_cli.load_secret(settings),
                "domain": "feishu",
                "connection_mode": "websocket",
                "dm_policy": "disabled",
                "group_policy": "disabled",
            },
        )
    )


async def pair(config: Config, channel: dict[str, Any]) -> dict[str, Any]:
    upstream = importlib.import_module("plugins.platforms.feishu.adapter")
    value = await asyncio.wait_for(
        feishu_pairing.run(
            config,
            configuration.feishu_settings(config, channel),
            store.home(config, channel["id"]),
            feishu_gateway_cli.load_secret(configuration.feishu_settings(config, channel)),
            channel["challenge"],
            upstream,
            importlib.import_module("gateway.config").PlatformConfig,
        ),
        600,
    )
    if value["state"] != "candidate_received_needs_local_confirmation":
        raise InvalidInput("channel_pairing_not_completed")
    from ..query_routing_files import read

    result: dict[str, Any] = json.loads(
        read(store.home(config, channel["id"]) / "pairing-candidate.json", 4096)
    )
    return result


async def send(
    adapter: Any, owner: str, body: str | dict[str, Any], context: str, guard: Any
) -> bool:
    upstream = importlib.import_module("plugins.platforms.feishu.adapter")

    from ..feishu_sync import run

    if isinstance(body, dict) and body.get("schema") == "fiber-expert-feishu-output/v1":
        from ..expert_feishu_render import send as rich_send

        result = await run(rich_send, adapter._client, owner, body, guard)
    else:
        if not isinstance(body, str):
            raise InvalidInput("channel_message_format_invalid")
        result = await run(
            feishu_delivery.sdk_send, upstream.lark, adapter._client, owner, body, guard
        )
    return result.get("category") == "confirmed"
