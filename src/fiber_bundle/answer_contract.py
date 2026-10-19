"""Exact quotation contract shared by deterministic answers and bounded model selections."""

from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError

from .answer_output_diagnostics import schema_issues
from .config import IntegrityError
from .payloads import Payload

SelectionReason = Literal[
    "schema", "unknown_citation", "duplicate_citation", "quote_mismatch", "omitted_evidence"
]


class SelectionRejected(IntegrityError):
    def __init__(self, reason: SelectionReason, issues: list[dict[str, str]] | None = None):
        self.reason = reason
        self.schema_issues = issues or []
        super().__init__("answer_selection_" + reason)


class Quotation(Payload):
    citation: Annotated[str, Field(min_length=3, max_length=8)]
    quote: Annotated[str, Field(min_length=1, max_length=400)]


class Selection(Payload):
    mode: Literal["extractive"]
    quotations: Annotated[list[Quotation], Field(max_length=20)]


def validate_selection(value: object, evidence: list[dict[str, Any]]) -> list[dict[str, str]]:
    """No free-form claims: a selection may only repeat supplied evidence verbatim."""
    try:
        selection = Selection.model_validate(value)
    except ValidationError as exc:
        raise SelectionRejected("schema", schema_issues(exc)) from None
    available = {item["citation"]: item["snippet"] for item in evidence}
    used: set[str] = set()
    result = []
    for item in selection.quotations:
        if item.citation not in available:
            raise SelectionRejected("unknown_citation")
        if item.citation in used:
            raise SelectionRejected("duplicate_citation")
        if item.quote != available[item.citation]:
            raise SelectionRejected("quote_mismatch")
        used.add(item.citation)
        result.append({"citation": item.citation, "quote": item.quote})
    # Evidence comparisons cannot silently disappear through selective quotation.
    if used != set(available):
        raise SelectionRejected("omitted_evidence")
    return result
