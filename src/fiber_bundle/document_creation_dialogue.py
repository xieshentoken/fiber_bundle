"""Fixed two-round Pi dialogue validation, including complete requests and draft bytes."""

import json
from typing import Any

from .answer_model_output import reject_constant, unique_object
from .config import InvalidInput
from .document_creation_contract import MAX_ARTIFACT_BYTES
from .document_creation_output import DRAFT_MARKER
from .execution_config import ExecutionConfig
from .inputs import canonical
from .model_gateway import prepare

MAX_RESPONSE_BYTES = 512 * 1024
TOOL = {
    "type": "function",
    "function": {
        "name": "fiber_write_draft",
        "description": (
            "Write one unverified UTF-8 draft to the fixed task output. "
            "No paths, source retrieval, publication or revision tools are available."
        ),
        "parameters": {
            "type": "object",
            "properties": {"draft": {"type": "string", "minLength": 1}},
            "required": ["draft"],
            "additionalProperties": False,
        },
    },
}


def prompt(fixed: str) -> str:
    value = json.loads(fixed)
    result = value["messages"][0]["content"] + "\n" + value["messages"][1]["content"]
    if not isinstance(result, str):
        raise InvalidInput("document_fixed_prompt_invalid")
    return result


def response(policy: ExecutionConfig, value: object, ordinal: int) -> dict[str, Any]:
    try:
        raw = canonical(value).encode()
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError()
        data = json.loads(raw, parse_constant=reject_constant)
        if (
            not isinstance(data, dict)
            or data.get("model") != policy.model
            or not isinstance(data.get("choices"), list)
            or len(data["choices"]) != 1
        ):
            raise ValueError()
        choice = data["choices"][0]
        msg = choice["message"]
        if (
            type(choice["index"]) is not int
            or choice["index"] != 0
            or not isinstance(msg, dict)
            or msg.get("role") != "assistant"
        ):
            raise ValueError()
        if ordinal == 1:
            if (
                set(msg) != {"role", "content", "tool_calls"}
                or msg["content"] is not None
                or choice["finish_reason"] != "tool_calls"
            ):
                raise ValueError()
            draft(data)
        elif ordinal == 2:
            if (
                set(msg) != {"role", "content"}
                or not isinstance(msg["content"], str)
                or choice["finish_reason"] != "stop"
            ):
                raise ValueError()
        else:
            raise ValueError()
        return data
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, InvalidInput):
        raise InvalidInput("document_model_response_invalid") from None


def draft(first_response: dict[str, Any]) -> tuple[str, bytes, dict[str, Any]]:
    try:
        calls = first_response["choices"][0]["message"]["tool_calls"]
        if not isinstance(calls, list) or len(calls) != 1:
            raise ValueError()
        call = calls[0]
        if (
            set(call) != {"id", "type", "function"}
            or not isinstance(call["id"], str)
            or not 1 <= len(call["id"]) <= 80
            or call["type"] != "function"
        ):
            raise ValueError()
        fn = call["function"]
        if (
            set(fn) != {"name", "arguments"}
            or fn["name"] != "fiber_write_draft"
            or not isinstance(fn["arguments"], str)
        ):
            raise ValueError()
        args = json.loads(
            fn["arguments"], object_pairs_hook=unique_object, parse_constant=reject_constant
        )
        if (
            not isinstance(args, dict)
            or set(args) != {"draft"}
            or not isinstance(args["draft"], str)
            or not args["draft"].strip()
        ):
            raise ValueError()
        content = (DRAFT_MARKER + args["draft"]).encode()
        if len(content) > MAX_ARTIFACT_BYTES or any(
            (ord(c) < 32 and c not in "\t\n\r") or ord(c) == 127 for c in args["draft"]
        ):
            raise ValueError()
        assistant = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{**call, "function": {**fn, "arguments": canonical(args)}}],
        }
        return call["id"], content, assistant
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise InvalidInput("document_draft_response_invalid") from None


def request(
    policy: ExecutionConfig,
    value: object,
    fixed: str,
    prior: tuple[dict[str, Any], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if hasattr(policy, "configuration_id"):
        from .document_model_dialogue import request as registered_request

        return registered_request(policy, value, fixed, prior)
    payload = prepare(policy, value)
    messages = payload["messages"]
    if canonical(payload.get("tools")) != canonical([TOOL]):
        raise InvalidInput("document_model_tools_invalid")
    if prior is None:
        if (
            len(messages) != 2
            or any(set(m) != {"role", "content"} for m in messages)
            or messages[0]["role"] != "system"
            or not isinstance(messages[0]["content"], str)
            or messages[1] != {"role": "user", "content": prompt(fixed)}
        ):
            raise InvalidInput("document_model_input_changed")
    else:
        first, body = prior
        call_id, content, assistant = draft(body)
        fmt = json.loads(fixed)["messages"][1]["content"]
        filename = "draft." + json.loads(fmt)["requested_format"]
        if (
            len(messages) != 4
            or messages[:2] != first["messages"]
            or messages[2] != assistant
            or set(messages[3]) != {"role", "content", "tool_call_id"}
            or messages[3]["role"] != "tool"
            or messages[3]["tool_call_id"] != call_id
        ):
            raise InvalidInput("document_model_dialogue_changed")
        try:
            result = json.loads(
                messages[3]["content"],
                object_pairs_hook=unique_object,
                parse_constant=reject_constant,
            )
            expected = {
                "status": "unverified_draft_written",
                "filename": filename,
                "byte_count": len(content),
            }
            if canonical(result) != canonical(expected):
                raise ValueError()
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise InvalidInput("document_model_tool_result_invalid") from None
    wire = json.dumps(payload, allow_nan=False).encode()
    if len(wire) > policy.max_request_bytes or len(wire) + 128 > policy.max_input_tokens:
        raise InvalidInput("document_actual_request_limit_exceeded")
    return payload
