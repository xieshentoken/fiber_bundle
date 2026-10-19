"""Explicit provider presets and model capabilities, separate from account discovery."""

import ipaddress
import re
from typing import Any
from urllib.parse import urlsplit

from .contracts import Connection
from .errors import ModelError

PRESETS = {
    "openai": ("OpenAI", "https://api.openai.com/v1", "openai-responses"),
    "anthropic": ("Anthropic", "https://api.anthropic.com/v1", "anthropic-messages"),
    "deepseek": ("DeepSeek", "https://api.deepseek.com/v1", "openai-completions"),
    "opencodego": ("OpenCode Go", "https://opencode.ai/zen/go/v1", "openai-completions"),
}


def connection(value: Connection) -> dict[str, str]:
    if value.provider in PRESETS:
        _, base, protocol = PRESETS[value.provider]
    else:
        base, protocol = value.base_url.rstrip("/"), value.protocol
    try:
        url = urlsplit(base)
        port = url.port
        if not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError()
        address = None
        try:
            address = ipaddress.ip_address(url.hostname)
        except ValueError:
            pass
        if value.provider == "local":
            if url.scheme not in ("http", "https") or not (address and address.is_loopback):
                raise ValueError()
        elif url.scheme != "https":
            raise ValueError()
        if port is not None and not 1 <= port <= 65535:
            raise ValueError()
        if any(ord(c) < 33 for c in base) or url.hostname in {"metadata.google.internal"}:
            raise ValueError()
        if address and (address.is_link_local or address.is_multicast or address.is_unspecified):
            raise ModelError("model_endpoint_forbidden")
        if value.provider != "local" and address and address.is_loopback:
            raise ModelError("model_endpoint_forbidden")
    except (ValueError, TypeError):
        raise ModelError("model_endpoint_invalid") from None
    return {"provider": value.provider, "base_url": base, "protocol": protocol}


def capabilities(provider: str, model: str, protocol: str) -> dict[str, Any]:
    """Conservative documented families; model listing itself is not capability proof."""
    effort: list[str] = []
    if provider == "openai" and re.match(r"^(gpt-[5-9]|o[134])", model):
        effort = ["low", "medium", "high"]
    elif provider == "anthropic" and re.match(r"^claude-(opus|sonnet)-(4-6|[5-9])", model):
        effort = ["low", "medium", "high"]
    elif provider == "deepseek" and ("reasoner" in model or "v4" in model):
        effort = ["low", "high"]
    if protocol not in {"openai-completions", "openai-responses", "anthropic-messages"}:
        raise ModelError("model_protocol_unsupported")
    excluded = any(
        x in model.lower() for x in ("embedding", "whisper", "tts", "dall-e", "image", "realtime")
    )
    return {
        "efforts": ["default", *effort],
        "capability_source": "fiber-provider-rules/v1",
        "compatible": not excluded,
        "tools": "unverified",
    }


def check_effort(provider: str, model: str, protocol: str, effort: str) -> None:
    if effort not in capabilities(provider, model, protocol)["efforts"]:
        raise ModelError("model_effort_unsupported")
