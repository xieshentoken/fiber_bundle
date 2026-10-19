"""Host-built local answer requests and bounded completion envelopes; no I/O."""

import json
from typing import Any

from .answer_model_output import parse_response
from .config import InvalidInput
from .query_routing_config import LocalQueryConfig


def prepare(local: LocalQueryConfig, value: object) -> dict[str, Any]:
    local.validate()
    if not isinstance(value, dict) or set(value) != {"messages"}:
        raise InvalidInput("local_request_invalid")
    messages = value["messages"]
    if not isinstance(messages, list) or len(messages) != 2:
        raise InvalidInput("local_request_invalid")
    for message, role in zip(messages, ("system", "user"), strict=True):
        if (
            not isinstance(message, dict)
            or set(message) != {"role", "content"}
            or message["role"] != role
            or not isinstance(message["content"], str)
            or not message["content"].strip()
        ):
            raise InvalidInput("local_request_invalid")
    result = {
        "model": local.model,
        "messages": [dict(message) for message in messages],
        "max_tokens": local.max_output_tokens,
        "stream": False,
    }
    try:
        if len(json.dumps(result, allow_nan=False).encode("utf-8")) > local.max_request_bytes:
            raise InvalidInput("local_request_too_large")
        for message in messages:
            message["content"].encode("utf-8")
    except UnicodeError as exc:
        raise InvalidInput("local_request_invalid") from exc
    return result


def accept(local: LocalQueryConfig, value: object) -> dict[str, Any]:
    """Validate transport only: citation/evidence validation still belongs to the host."""
    if not isinstance(value, dict) or value.get("model") != local.model:
        raise InvalidInput("local_response_invalid")
    parse_response(value)
    value["choices"][0]["message"]["content"].encode("utf-8")
    usage = value.get("usage")
    if not isinstance(usage, dict):
        raise InvalidInput("local_usage_invalid")
    incoming, outgoing = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if (
        type(incoming) is not int
        or type(outgoing) is not int
        or not 0 <= incoming <= local.max_input_tokens
        or not 0 < outgoing <= local.max_output_tokens
    ):
        raise InvalidInput("local_usage_invalid")
    # Provider debug text, reasoning, IDs and unknown fields never enter the Pi reply.
    return {
        "id": "local-completion",
        "object": "chat.completion",
        "created": 0,
        "model": local.model,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": value["choices"][0]["message"]["content"],
                },
            }
        ],
        "usage": {
            "prompt_tokens": incoming,
            "completion_tokens": outgoing,
            "total_tokens": incoming + outgoing,
        },
    }
