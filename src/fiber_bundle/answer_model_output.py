"""Bounded provider response validation with content-free rejection diagnostics."""

import json
from typing import Any, Literal

from .answer_contract import SelectionRejected, validate_selection
from .config import InvalidInput

Reason = Literal[
    "envelope",
    "unfinished",
    "role",
    "tools",
    "content",
    "json",
    "schema",
    "unknown_citation",
    "duplicate_citation",
    "quote_mismatch",
    "omitted_evidence",
]
JSONReason = Literal[
    "duplicate_member", "nonstandard_constant", "markdown_fence", "syntax", "nesting_limit"
]


class JSONContentRejected(ValueError):
    def __init__(self, reason: JSONReason):
        self.reason = reason
        super().__init__(reason)


class AnswerOutputRejected(InvalidInput):
    def __init__(
        self,
        reason: Reason,
        issues: list[dict[str, str]] | None = None,
        *,
        json_issue: JSONReason | None = None,
    ):
        self.code = "answer_output_rejected_" + reason
        self.schema_issues = issues or []
        self.json_issue = json_issue
        super().__init__(self.code)


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise JSONContentRejected("duplicate_member")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise JSONContentRejected("nonstandard_constant")


def parse_response(body: object) -> object:
    """Validate the provider envelope and decode bounded JSON, without trusting its claims."""
    if not isinstance(body, dict):
        raise AnswerOutputRejected("envelope")
    choices = body.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise AnswerOutputRejected("envelope")
    choice = choices[0]
    if choice.get("finish_reason") != "stop":
        raise AnswerOutputRejected("unfinished")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise AnswerOutputRejected("envelope")
    if message.get("role") != "assistant":
        raise AnswerOutputRejected("role")
    if message.get("tool_calls") or message.get("function_call"):
        raise AnswerOutputRejected("tools")
    content = message.get("content")
    # At most 20 x 400-character quotes; allow full JSON escaping with bounded overhead.
    if not isinstance(content, str) or not content or len(content) > 65536:
        raise AnswerOutputRejected("content")
    try:
        proposal = json.loads(
            content, object_pairs_hook=unique_object, parse_constant=reject_constant
        )
    except JSONContentRejected as exc:
        raise AnswerOutputRejected("json", json_issue=exc.reason) from None
    except json.JSONDecodeError:
        issue: JSONReason = "markdown_fence" if content.lstrip().startswith("```") else "syntax"
        raise AnswerOutputRejected("json", json_issue=issue) from None
    except RecursionError:
        raise AnswerOutputRejected("json", json_issue="nesting_limit") from None
    except ValueError:
        # The standard decoder also rejects integers beyond Python's digit limit.
        raise AnswerOutputRejected("json", json_issue="syntax") from None
    return proposal


def validate_response(body: object, evidence: list[dict[str, Any]]) -> list[dict[str, str]]:
    proposal = parse_response(body)
    try:
        return validate_selection(proposal, evidence)
    except SelectionRejected as exc:
        raise AnswerOutputRejected(exc.reason, exc.schema_issues) from None
