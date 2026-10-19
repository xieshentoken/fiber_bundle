"""Bounded executor metadata. Never retain prompts, tool content, provider bodies or paths."""

from typing import Any

TOOLS = {"read", "write", "edit", "fiber_capture_article", "fiber_extract_document"}
STOPS = {"stop", "length", "tool_calls", "toolUse", "error", "aborted", "content_filter"}


def known(value: Any, allowed: set[str]) -> str:
    return value if isinstance(value, str) and value in allowed else "unknown"


def model(reply: dict[str, Any]) -> dict[str, Any]:
    body = reply.get("body", {})
    body = body if isinstance(body, dict) else {}
    choices = body.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else {}
    choice = choice if isinstance(choice, dict) else {}
    message = choice.get("message")
    message = message if isinstance(message, dict) else {}
    tools = message.get("tool_calls")
    tools = tools if isinstance(tools, list) else []
    names = []
    for tool in tools[:16]:
        function = tool.get("function") if isinstance(tool, dict) else None
        names.append(known(function.get("name") if isinstance(function, dict) else None, TOOLS))
    content = message.get("content")
    return {
        "event": "model_reply",
        "finish": known(choice.get("finish_reason"), STOPS),
        "tools": names,
        "content_chars": len(content) if isinstance(content, str) else 0,
    }


def event(value: dict[str, Any]) -> dict[str, Any] | None:
    kind = value.get("type")
    if kind not in {"tool_execution_start", "tool_execution_end", "agent_end"}:
        return None
    result: dict[str, Any] = {"event": kind}
    if kind == "agent_end":
        raw = value.get("messages")
        messages = [
            m
            for m in (raw if isinstance(raw, list) else [])
            if isinstance(m, dict) and m.get("role") == "assistant"
        ]
        reason = messages[-1].get("stopReason") if messages else None
        result["stop"] = known(reason, STOPS)
    else:
        tool = value.get("toolName")
        result["tool"] = known(tool, TOOLS)
        result["error"] = value.get("isError") is True
        args = value.get("args")
        path = args.get("path") if isinstance(args, dict) else None
        path = path if isinstance(path, str) else None
        result["target"] = (
            "draft"
            if path in {"draft.json", "/task/draft.json"}
            else ("input" if path in {"input.json", "/task/input.json"} else "other")
        )
        if kind == "tool_execution_start" and tool == "write":
            content = args.get("content") if isinstance(args, dict) else None
            result["content_type"] = (
                "string"
                if isinstance(content, str)
                else "object"
                if isinstance(content, dict)
                else "missing_or_other"
            )
            result["content_chars"] = len(content) if isinstance(content, str) else 0
        # Extract only known system error identifiers, not untrusted tool text.
        text = str(value.get("result", {}))
        result["io_errors"] = [
            code
            for code in ("EACCES", "ENOENT", "EROFS", "ENOSPC", "ENAMETOOLONG", "EFBIG")
            if code in text
        ]
        if result["error"]:
            lowered = text.casefold()
            result["error_kind"] = (
                "io"
                if result["io_errors"]
                else "arguments"
                if any(
                    s in lowered
                    for s in (
                        "validation",
                        "invalid arguments",
                        "required property",
                        "expected string",
                        "must be string",
                    )
                )
                else "json"
                if any(s in lowered for s in ("json", "unexpected token"))
                else "aborted"
                if "aborted" in lowered
                else "unknown"
            )
    return result
