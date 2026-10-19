"""Validate native draft dialogue through the existing fixed semantic contract."""

from copy import deepcopy
from typing import Any

from .config import InvalidInput
from .execution_config import ExecutionConfig
from .inputs import canonical


def text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(
        isinstance(b, dict) and b.get("type") in {"text", "input_text", "output_text"}
        for b in value
    ):
        return "".join(b["text"] for b in value)
    raise InvalidInput("document_native_text_invalid")


def canonical_request(value: dict[str, Any], protocol: str) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    if protocol == "anthropic-messages":
        if value.get("system"):
            messages.append({"role": "system", "content": text(value["system"])})
        for message in value["messages"]:
            blocks = message["content"]
            if isinstance(blocks, str):
                messages.append({"role": message["role"], "content": blocks})
                continue
            tools = [b for b in blocks if b["type"] == "tool_use"]
            results = [b for b in blocks if b["type"] == "tool_result"]
            if tools:
                messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": b["id"],
                                "type": "function",
                                "function": {"name": b["name"], "arguments": canonical(b["input"])},
                            }
                            for b in tools
                        ],
                    }
                )
            elif results:
                messages.extend(
                    {
                        "role": "tool",
                        "tool_call_id": b["tool_use_id"],
                        "content": text(b["content"]),
                    }
                    for b in results
                )
            else:
                messages.append({"role": message["role"], "content": text(blocks)})
        tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in value.get("tools", [])
        ]
    elif protocol == "openai-responses":
        if value.get("instructions"):
            messages.append({"role": "system", "content": value["instructions"]})
        for item in value["input"]:
            kind = item.get("type", "message")
            if kind == "message":
                messages.append(
                    {
                        "role": "system" if item["role"] == "developer" else item["role"],
                        "content": text(item["content"]),
                    }
                )
            elif kind == "function_call":
                messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": item["call_id"],
                                "type": "function",
                                "function": {"name": item["name"], "arguments": item["arguments"]},
                            }
                        ],
                    }
                )
            elif kind == "function_call_output":
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": item["call_id"],
                        "content": text(item["output"]),
                    }
                )
            elif kind != "reasoning":
                raise InvalidInput("document_native_item_invalid")
        tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                },
            }
            for t in value.get("tools", [])
        ]
    else:
        messages = [
            {**m, "role": "system" if m["role"] == "developer" else m["role"]}
            for m in value["messages"]
        ]
        tools = [
            {
                "type": t["type"],
                "function": {k: t["function"][k] for k in ("name", "description", "parameters")},
            }
            for t in value.get("tools", [])
        ]
    return {"messages": messages, "tools": tools}


def request(
    policy: ExecutionConfig,
    value: object,
    fixed: str,
    prior: tuple[dict[str, Any], dict[str, Any]] | None,
) -> dict[str, Any]:
    from . import document_creation_dialogue as dialogue
    from .model_gateway import prepare

    if not isinstance(value, dict):
        raise InvalidInput("document_native_request_invalid")
    value = deepcopy(value)
    protocol = getattr(policy, "protocol", "openai-completions")
    # The pinned Anthropic SDK omits this flag. Restore the fixed constraint in
    # the final wire request; the draft tool independently rejects extra fields.
    for tool in value.get("tools", []):
        function = tool.get("function", tool)
        if function.get("name") == "fiber_write_draft":
            schema = function.get("input_schema", function.get("parameters"))
            if isinstance(schema, dict) and "additionalProperties" not in schema:
                schema["additionalProperties"] = False
    converted = canonical_request(value, protocol)
    old = ExecutionConfig(**{k: getattr(policy, k) for k in ExecutionConfig.__dataclass_fields__})
    history = (prepare(old, canonical_request(prior[0], protocol)), prior[1]) if prior else None
    dialogue.request(old, converted, fixed, history)
    if prior and protocol in {"anthropic-messages", "openai-responses"}:
        raw = prior[1].get("_native_response", {})
        previous = (
            raw.get("content", []) if protocol == "anthropic-messages" else raw.get("output", [])
        )
        blocks = (
            [
                b
                for m in value.get("messages", [])
                for b in m.get("content", [])
                if isinstance(b, dict)
            ]
            if protocol == "anthropic-messages"
            else value.get("input", [])
        )
        for thought in previous:
            if thought.get("type") in {"thinking", "reasoning"}:
                field = "signature" if protocol == "anthropic-messages" else "encrypted_content"
                if thought.get(field) and not any(b.get(field) == thought[field] for b in blocks):
                    raise InvalidInput("document_native_reasoning_changed")
    result = prepare(policy, value)
    if len(canonical(result).encode()) + 128 > policy.max_input_tokens:
        raise InvalidInput("document_actual_request_limit_exceeded")
    return result
