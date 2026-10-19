"""Bounded body samples with offsets into the unchanged Markdown source."""

import re
from dataclasses import dataclass

from ..config import InvalidInput
from .contracts import Source
from .results import Span, line_at

FRAGMENT_CHARS = 120
MODEL_TOKENS = 512
NON_BODY = re.compile(
    r"^(?:!\[|```|~~~|[-*]\s*(?:上位主题|相关概念|相关项目)\s*[:：]|"
    r">\s*(?:\*\*)?(?:来源|原文|抓取时间|整理时间|公众号))"
)


@dataclass(frozen=True)
class Sample:
    text: str
    evidence: tuple[Span, ...]


def body_sample(note: Source) -> Sample:
    lines = note.text.splitlines(keepends=True)
    first = 0
    if lines and lines[0].strip().lstrip("\ufeff") == "---":
        first = next(
            (i + 1 for i, line in enumerate(lines[1:], 1) if line.strip() in {"---", "..."}),
            len(lines),
        )
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    blocks: list[tuple[int, int]] = []
    start: int | None = None
    count = 0
    for i in range(first, len(lines)):
        stripped = lines[i].strip()
        # Headings are represented by the title; metadata and empty lines are not body.
        if (
            not stripped
            or stripped.startswith("#")
            or stripped == "---"
            or NON_BODY.match(stripped)
        ):
            if start is not None:
                blocks.append((start, offsets[i]))
                start, count = None, 0
            continue
        if start is None:
            start = offsets[i]
        count += 1
        if count == 3:
            blocks.append((start, offsets[i + 1]))
            start, count = None, 0
    if start is not None:
        blocks.append((start, len(note.text)))
    if not blocks:
        raise InvalidInput("graph_body_unavailable")
    # Prefer visible passages that discuss the title's terms. Section anchors break ties
    # so generic/short titles still cover the beginning, middle, and ending.
    title = note.title.casefold()
    terms = {
        title[i : i + 2]
        for i in range(len(title) - 1)
        if all(c.isalnum() for c in title[i : i + 2])
    }
    anchors = [0, (len(blocks) - 1) // 2, len(blocks) - 1]

    def rank(index: int) -> tuple[int, int, int]:
        start, end = blocks[index]
        visible = note.text[start : min(end, start + FRAGMENT_CHARS)].casefold()
        return (
            -sum(term in visible for term in terms),
            min(abs(index - a) for a in anchors),
            index,
        )

    chosen = sorted(sorted(range(len(blocks)), key=rank)[:3])
    evidence = []
    for index in chosen:
        start, end = blocks[index]
        while start < end and note.text[start].isspace():
            start += 1
        end = min(end, start + FRAGMENT_CHARS)
        while end > start and note.text[end - 1].isspace():
            end -= 1
        if start == end:
            continue
        evidence.append(
            Span(
                note.note_id,
                note.sha256,
                start,
                end,
                line_at(note.text, start),
                line_at(note.text, end - 1),
                note.text[start:end],
            )
        )
    if not evidence:
        raise InvalidInput("graph_body_unavailable")
    return Sample(note.title[:80] + "\n" + "\n".join(s.quote for s in evidence), tuple(evidence))
