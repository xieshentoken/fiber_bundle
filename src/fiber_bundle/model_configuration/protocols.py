"""Bounded provider payloads and lossless native replies for the restricted Pi bridge."""

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ..execution_config import read_secret
from .discovery import NoRedirect, guard_address, headers, http_error
from .errors import ModelError


def prepare(policy: Any, value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ModelError("model_response_invalid")
    native = value.get("_fiber_native") == policy.protocol
    if native:
        allowed = {
            "messages",
            "input",
            "system",
            "tools",
            "tool_choice",
            "parallel_tool_calls",
            "include",
            "text",
            "thinking",
            "instructions",
        }
        result = {k: v for k, v in value.items() if k in allowed}
    else:
        messages = value.get("messages")
        if not isinstance(messages, list) or not 0 < len(messages) <= 128:
            raise ModelError("model_response_invalid")
        if policy.protocol == "anthropic-messages":
            result = {"messages": [m for m in messages if m.get("role") != "system"]}
            system = "\n".join(m.get("content", "") for m in messages if m.get("role") == "system")
            if system:
                result["system"] = system
        elif policy.protocol == "openai-responses":
            result = {"input": messages, "store": False}
        else:
            result = {"messages": messages}
        if value.get("tools"):
            result["tools"] = value["tools"]
    result.update(model=policy.model, stream=False)
    if native:
        result["_fiber_native"] = policy.protocol
    if policy.protocol == "openai-responses":
        result.update(max_output_tokens=policy.max_output_tokens, store=False)
        if policy.effort != "default":
            result["reasoning"] = {"effort": policy.effort}
    elif policy.protocol == "anthropic-messages":
        result["max_tokens"] = policy.max_output_tokens
        if policy.effort != "default":
            result["thinking"] = {"type": "adaptive"}
            result["output_config"] = {"effort": policy.effort}
    else:
        result["max_tokens"] = policy.max_output_tokens
        if policy.effort != "default":
            result["reasoning_effort"] = policy.effort
            if policy.provider == "deepseek":
                result["thinking"] = {"type": "enabled"}
    if len(json.dumps(result).encode()) > policy.max_request_bytes:
        raise ModelError("model_response_invalid")
    return result


def normalize(protocol: str, raw: dict[str, Any]) -> dict[str, Any]:
    if protocol == "openai-completions":
        if not raw.get("choices"):
            raise ModelError("model_response_invalid")
        return raw
    usage = raw.get("usage")
    normalized_usage = None
    if isinstance(usage, dict):
        incoming, outgoing = usage.get("input_tokens"), usage.get("output_tokens")
        if type(incoming) is int and type(outgoing) is int and incoming >= 0 and outgoing >= 0:
            if protocol == "anthropic-messages":
                incoming += usage.get("cache_creation_input_tokens", 0) + usage.get(
                    "cache_read_input_tokens", 0
                )
            normalized_usage = {
                "prompt_tokens": incoming,
                "completion_tokens": outgoing,
                "total_tokens": incoming + outgoing,
            }
    calls = []
    text = []
    if protocol == "anthropic-messages":
        blocks = raw.get("content", [])
        for block in blocks:
            if block.get("type") == "text":
                text.append(block["text"])
            elif block.get("type") == "tool_use":
                calls.append(
                    {
                        "id": block["id"],
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(block["input"]),
                        },
                    }
                )
        finish = (
            "tool_calls"
            if raw.get("stop_reason") == "tool_use"
            else "stop"
            if raw.get("stop_reason") == "end_turn"
            else "length"
        )
    else:
        for item in raw.get("output", []):
            if item.get("type") == "message":
                text.extend(
                    b["text"] for b in item.get("content", []) if b.get("type") == "output_text"
                )
            elif item.get("type") == "function_call":
                calls.append(
                    {
                        "id": item["call_id"],
                        "type": "function",
                        "function": {"name": item["name"], "arguments": item["arguments"]},
                    }
                )
        finish = "tool_calls" if calls else "stop" if raw.get("status") == "completed" else "length"
    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(text) if text or not calls else None,
    }
    if calls:
        message["tool_calls"] = calls
    return {
        "id": raw.get("id"),
        "model": raw.get("model"),
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": normalized_usage,
        "_native_response": raw,
    }


def complete(
    policy: Any, run_id: str, payload: dict[str, Any], *, loaded_key: str | None = None
) -> dict[str, Any]:
    policy.validate()
    guard_address(policy.endpoint, policy.provider == "local")
    key = loaded_key if loaded_key is not None else read_secret(Path(policy.secret_file))
    request_headers = headers(
        "anthropic" if policy.protocol == "anthropic-messages" else policy.provider, key
    )
    request_headers["Content-Type"] = "application/json"
    if policy.provider == "opencodego":
        # Go requires one stable session identity for every inference request, including tests.
        request_headers["x-opencode-session"] = run_id
    request = urllib.request.Request(
        policy.endpoint,
        data=json.dumps({k: v for k, v in payload.items() if not k.startswith("_fiber_")}).encode(),
        headers=request_headers,
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=min(policy.timeout_seconds, 120)) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ModelError("model_response_invalid")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ModelError("model_response_invalid")
        return normalize(policy.protocol, data)
    except urllib.error.HTTPError as exc:
        raise http_error(exc) from None
    except TimeoutError:
        raise ModelError("model_timeout") from None
    except urllib.error.URLError as exc:
        raise ModelError(
            "model_timeout" if isinstance(exc.reason, TimeoutError) else "model_transport_failure"
        ) from None
    except OSError:
        raise ModelError("model_transport_failure") from None
    except (ValueError, KeyError, TypeError):
        raise ModelError("model_response_invalid") from None
