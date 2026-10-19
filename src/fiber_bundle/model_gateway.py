"""Bounded OpenCode Go calls on the host; no redirect, credential or content logging."""

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from fiber_bundle.config import InvalidInput
from fiber_bundle.execution_config import ExecutionConfig, read_secret


class ModelFailure(InvalidInput):
    """Safe diagnostic code; provider bodies and credential-bearing requests stay private."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def prepare(policy: ExecutionConfig, value: object) -> dict[str, Any]:
    if hasattr(policy, "configuration_id"):
        from .model_configuration.protocols import prepare as registered_prepare

        return registered_prepare(policy, value)
    if not isinstance(value, dict) or not isinstance(value.get("messages"), list):
        raise InvalidInput("invalid model request")
    messages = value["messages"]
    if not messages or len(messages) > 128:
        raise InvalidInput("invalid model messages")
    messages = [dict(message) if isinstance(message, dict) else message for message in messages]
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in (
            "system",
            "user",
            "assistant",
            "tool",
        ):
            raise InvalidInput("invalid message")
        content = message.get("content")
        if isinstance(content, list):
            if not all(
                isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
                for block in content
            ):
                raise InvalidInput("M3 supports text only")
            message["content"] = "\n".join(block["text"] for block in content)
            content = message["content"]
        if content is not None and not isinstance(content, str):
            raise InvalidInput("M3 supports text only")
    result = {
        "model": policy.model,
        "messages": messages,
        "max_tokens": policy.max_output_tokens,
        "stream": False,
    }
    if "tools" in value:
        if not isinstance(value["tools"], list) or len(value["tools"]) > 16:
            raise InvalidInput("invalid tools")
        result["tools"] = value["tools"]
    # Never forward caller-supplied endpoints, keys, session IDs, or unbounded generation settings.
    if len(json.dumps(result, ensure_ascii=False).encode()) > policy.max_request_bytes:
        raise InvalidInput("request too large")
    return result


def complete(
    policy: ExecutionConfig,
    run_id: str,
    payload: dict[str, Any],
    *,
    require_message: bool = True,
) -> dict[str, Any]:
    if hasattr(policy, "configuration_id"):
        from .model_configuration.protocols import complete as registered_complete

        return registered_complete(policy, run_id, payload)
    key = read_secret(Path(policy.secret_file))
    request = urllib.request.Request(
        policy.endpoint,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "User-Agent": "fiber-bundle-pi/0.3.0",
            "x-opencode-session": run_id,
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=min(policy.timeout_seconds, 120)) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise InvalidInput("model response too large")
        data = json.loads(raw)
        if not isinstance(data, dict) or (
            require_message and not isinstance(data.get("choices"), list)
        ):
            raise InvalidInput("invalid model response")
        if require_message and (
            not data["choices"]
            or not isinstance(data["choices"][0], dict)
            or not isinstance(data["choices"][0].get("message"), dict)
        ):
            raise InvalidInput("missing model message")
        return data
    except urllib.error.HTTPError as exc:
        raise ModelFailure(f"model_http_{exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ModelFailure("model_transport_failure") from None
    except ValueError:
        # Do not include exception messages, provider bodies, or request objects.
        raise InvalidInput("model request failed; reservation retained") from None
