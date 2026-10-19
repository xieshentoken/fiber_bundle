"""Shared literal query matching for Vault and session evidence."""

import re
import unicodedata

from .config import InvalidInput
from .note_index import adjacent_passages, passages


def terms(query: str) -> list[re.Pattern[str]]:
    if (
        not isinstance(query, str)
        or not 1 <= len(query) <= 200
        or any(unicodedata.category(char) == "Cc" for char in query)
    ):
        raise InvalidInput("invalid search query")
    words = list(dict.fromkeys(fold(query).split()))
    if not 1 <= len(words) <= 8 or any(not any(c.isalnum() for c in word) for word in words):
        raise InvalidInput("search requires one to eight literal terms")
    return [
        re.compile(
            (r"(?<![a-z0-9_])" + re.escape(word) + r"(?![a-z0-9_])")
            if word.isascii()
            else re.escape(word)
        )
        for word in words
    ]


def fold(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def best_passage(
    title: str, content: str, patterns: list[re.Pattern[str]]
) -> tuple[int, int, str] | None:
    title = fold(title)
    best = None
    best_is_heading = False
    from itertools import chain

    for line, passage in chain(passages(content), adjacent_passages(content)):
        body = fold(passage)
        if all(p.search(title) or p.search(body) for p in patterns):
            score = sum(3 * bool(p.search(title)) + bool(p.search(body)) for p in patterns)
            is_heading = re.match(r"^\s{0,3}#{1,6}(?:\s|$)", passage) is not None
            # Equal literal scores should retain substantive text instead of its heading.
            if (
                best is None
                or score > best[0]
                or (score == best[0] and best_is_heading and not is_heading)
                or (
                    score == best[0]
                    and is_heading == best_is_heading
                    and len(passage) < len(best[2])
                )
            ):
                best = (score, line, passage)
                best_is_heading = is_heading
    return best
