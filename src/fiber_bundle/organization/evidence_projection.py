"""Map presentation-equivalent quotes back to exact, authorized source fragments."""

import re
from typing import Any

from . import text_features

PUNCTUATION = str.maketrans("：，；！？（）", ":,;!?()")
SPACING = re.compile(r"[\u3400-\u9fff。！？；：、（）《》「」“”:;!?()]")


def projection(text: str, *, lists: bool = False) -> tuple[str, list[int]]:
    omitted: set[int] = set()
    # Paired inline emphasis only: operators, code, numbers and words are retained.
    for match in re.finditer(r"(?<![\\*])([*]{2}|__)(\S(?:.*?\S)?)\1", text):
        omitted.update(range(match.start(), match.start() + 2))
        omitted.update(range(match.end() - 2, match.end()))
    if lists:
        for match in re.finditer(r"(?m)^\s*[-*]\s+", text):
            omitted.update(range(match.start(), match.end()))
    offsets = [i for i in range(len(text)) if i not in omitted]
    clean = "".join(text[i] for i in offsets)
    chars, positions = [], []
    for match in re.finditer(r"\s+|\S", clean):
        value = match[0]
        if value.isspace():
            adjacent = (
                clean[match.start() - 1 : match.start()] + clean[match.end() : match.end() + 1]
            )
            if SPACING.search(adjacent):
                continue
            value = " "
        chars.append(value.translate(PUNCTUATION))
        positions.append(offsets[match.start()])
    return "".join(chars), positions


def fragments(text: str, quote: str, parts: Any, *, lists: bool = False) -> list[Any]:
    projected: list[str] = []
    locations: list[int] = []
    for part in parts:
        value, offsets = projection(text[part["start"] : part["end"]], lists=lists)
        if projected and value and not SPACING.search(projected[-1][-1:] + value[:1]):
            projected.append(" ")
            locations.append(part["start"])
        projected.append(value)
        locations.extend(part["start"] + p for p in offsets)
    needle, _ = projection(quote.strip(), lists=lists)
    joined = "".join(projected)
    found = joined.find(needle) if needle else -1
    if found < 0:
        return []
    start, end = locations[found], locations[found + len(needle) - 1] + 1
    return [
        {
            "start": max(start, p["start"]),
            "end": min(end, p["end"]),
            "quote": text[max(start, p["start"]) : min(end, p["end"])],
        }
        for p in parts
        if max(start, p["start"]) < min(end, p["end"])
    ]


def body_spans(text: str, quote: str, *, windows: Any = None) -> list[Any]:
    exact = text_features.source_spans(text, quote, windows=windows)
    if exact:
        return exact
    parts = [p for p in text_features.passages(text) if p["kind"] == "body"]
    result = fragments(text, quote, parts)
    if windows is not None:
        for span in result:
            cursor = span["start"]
            for window in windows:
                if window["start"] > cursor:
                    break
                if window["end"] > cursor:
                    cursor = window["end"]
                if cursor >= span["end"]:
                    break
            if cursor < span["end"]:
                return []
    return result


def scope_spans(text: str, quote: str) -> list[Any]:
    parts = text_features.passages(text)
    # Explicit ellipses may separate independently verified definition fragments.
    # No unmarked substantive omissions are accepted.
    pieces = re.split(r"(?:…{1,}|\.{3,})", quote)
    spans: list[Any] = []
    for piece in pieces:
        if not piece.strip():
            continue
        matches = fragments(text, piece, parts, lists=True)
        if not matches or (spans and matches[0]["start"] < spans[-1]["end"]):
            return []
        spans.extend(matches)
    return spans
