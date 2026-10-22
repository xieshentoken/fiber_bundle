"""Bounded local query decomposition using only authorized note-title vocabulary."""

import re
from collections.abc import Iterable
from typing import Any

from ..config import InvalidInput
from ..search_matching import fold, terms

PARTS = re.compile(r"[\u3400-\u9fff]+|[^\s\u3400-\u9fff]+")
CHINESE = re.compile(r"[\u3400-\u9fff]+\Z")
MAX_ATTEMPTS = 12


def authorized_titles(config: Any, db: Any, channel: Any) -> list[str]:
    from .. import note_search_inventory
    from . import query_scope

    titles: list[str] = []
    for vault in query_scope.checked(config, db, channel):
        inventory = note_search_inventory.read(config, db, vault, selected=set())
        titles.extend(fold(note.title[:200]) for note in inventory.notes)
    return titles


def split(expression: str, titles: Iterable[str]) -> list[str]:
    """Longest title-supported Chinese spans; retain unknown text and Latin identifiers."""
    vocabulary = tuple(titles)
    result = []
    for part in PARTS.findall(fold(expression)):
        if not any(c.isalnum() for c in part):
            continue
        if not CHINESE.fullmatch(part) or len(part) < 4:
            result.append(part)
            continue
        offset = 0
        unknown = ""
        while offset < len(part):
            found = next(
                (
                    part[offset : offset + width]
                    for width in range(min(12, len(part) - offset), 1, -1)
                    if any(part[offset : offset + width] in title for title in vocabulary)
                ),
                None,
            )
            if found is None:
                unknown += part[offset]
                offset += 1
                continue
            if unknown:
                result.append(unknown)
                unknown = ""
            result.append(found)
            offset += len(found)
        if unknown:
            result.append(unknown)
    return list(dict.fromkeys(result))


def fallbacks(expressions: list[str], titles: Iterable[str]) -> tuple[list[str], list[str]]:
    """First retain all terms, then keep the leading topic with one other condition.

    These are retrieval candidates, never semantic claims. No character n-gram OR
    search, invented synonym, cross-vault vocabulary, or unbounded combinations.
    """
    vocabulary = tuple(titles)
    segmented: list[str] = []
    relaxed: list[str] = []
    for expression in expressions[:3]:
        tokens = split(expression, vocabulary)
        if not 1 < len(tokens) <= 8:
            continue
        joined = " ".join(tokens)
        try:
            terms(joined)
        except InvalidInput:
            continue
        if joined != fold(expression):
            segmented.append(joined)
        if len(tokens) > 2:
            # Prefer the final condition, retaining the original leading topic.
            relaxed.extend(tokens[0] + " " + other for other in reversed(tokens[1:]))
    return list(dict.fromkeys(segmented))[:3], list(dict.fromkeys(relaxed))[:3]
