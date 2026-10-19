"""Validate classification evidence without changing source facts or model requests."""

import json
import re
from typing import Any

from ..answer_model_output import reject_constant, unique_object
from ..config import InvalidInput
from . import evidence_projection

VERSION = "classification-evidence/v4-source-fragments"
_PUNCTUATION = str.maketrans("：，；！？（）", ":,;!?()")
_SPACING = re.compile(r"[\u3400-\u9fff，。！？；：、（）《》「」“”:,;!?()]")


class EvidenceInvalid(InvalidInput):
    """Keep the legacy public error code; expose only a bounded diagnostic reason."""

    def __init__(self, reason: str):
        super().__init__("organization_model_evidence_invalid")
        self.reason = reason


def _scope_projection(text: str) -> tuple[str, list[tuple[int, int]]]:
    characters, positions = [], []
    for token in re.finditer(r"\s+|\S", text):
        value = token[0]
        if value.isspace():
            adjacent = text[token.start() - 1 : token.start()] + text[token.end() : token.end() + 1]
            if _SPACING.search(adjacent):
                continue
            value = " "
        characters.append(value.translate(_PUNCTUATION))
        positions.append((token.start(), token.end()))
    return "".join(characters), positions


def _scope_span(text: str, quote: str) -> dict[str, Any] | None:
    start = text.find(quote)
    if start >= 0:
        return {"start": start, "end": start + len(quote), "quote": quote}
    projected, positions = _scope_projection(text)
    needle, _ = _scope_projection(quote.strip())
    found = projected.find(needle) if needle else -1
    if found < 0:
        return None
    start, end = positions[found][0], positions[found + len(needle) - 1][1]
    return {"start": start, "end": end, "quote": text[start:end]}


def validate(raw: str, body: str, profiles: Any, *, windows: Any = None) -> Any:
    try:
        fenced = re.fullmatch(r"\s*```(?:json)?\s*(.*?)\s*```\s*", raw, re.S | re.I)
        if fenced:
            raw = fenced[1]
        value = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
        if not isinstance(value, dict) or set(value) != {
            "moc_id",
            "evidence",
            "scope_quote",
            "reason",
        }:
            raise ValueError()
    except (ValueError, TypeError):
        raise EvidenceInvalid("format") from None
    if value["moc_id"] is None or value["moc_id"] == "null":
        return None
    profile = next((p for p in profiles if p["id"] == value["moc_id"]), None)
    if profile is None:
        raise EvidenceInvalid("moc_not_allowed")
    quotes = value["evidence"]
    if not isinstance(quotes, list) or len(quotes) < 2:
        raise EvidenceInvalid("body_quote_count")
    if not isinstance(value["reason"], str):
        raise EvidenceInvalid("format")
    spans: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    accepted = 0
    for index, quote in enumerate(quotes):
        if not isinstance(quote, str) or len(quote.strip()) < 6:
            rejected.append({"index": index, "reason": "body_quote_missing"})
            continue
        pieces = evidence_projection.body_spans(body, quote, windows=windows)
        if not pieces:
            rejected.append({"index": index, "reason": "body_quote_missing"})
            continue
        if any(
            piece["start"] < s["end"] and piece["end"] > s["start"]
            for piece in pieces
            for s in spans
        ):
            rejected.append({"index": index, "reason": "body_quote_overlap"})
            continue
        spans.extend(pieces)
        accepted += 1
    if accepted < 2:
        raise EvidenceInvalid(rejected[0]["reason"] if rejected else "body_quote_count")
    scope = value["scope_quote"]
    if not isinstance(scope, str) or len(scope.strip()) < 4:
        raise EvidenceInvalid("scope_quote_missing")
    matched = _scope_span(profile["text"], scope)
    scope_spans = [matched] if matched else evidence_projection.scope_spans(profile["text"], scope)
    if not scope_spans:
        raise EvidenceInvalid("scope_quote_missing")
    # Positions are relative to the MOC definition supplied to this request.
    # Keep original punctuation; never turn a normalized projection into evidence.
    return {
        **value,
        "evidence": spans,
        "scope_quote": "\n".join(s["quote"] for s in scope_spans),
        "scope_span": scope_spans[0],
        "scope_spans": scope_spans,
        "accepted_quotes": accepted,
        "rejected_quotes": rejected,
    }
