"""Source-exact procedure windows with heading context, without inferred steps."""

import re

from ..search_matching import fold, terms

INTENT = re.compile(r"流程|步骤|工艺|制备|\b(?:process|procedure|workflow|steps)\b", re.I)
HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
EXCLUDED = re.compile(
    r"概述|目录|关联|参考文献|文献来源|摘要|\b(?:overview|contents|references|abstract)\b", re.I
)
STEPS = re.compile(r"步骤|流程|后处理|烧结曲线|\b(?:steps|procedure|workflow)\b", re.I)
METHOD = re.compile(r"工艺原理|制备|工艺参数|\b(?:method|processing|parameters)\b", re.I)
ACTION = re.compile(
    r"混合|成型|加热|升温|保温|冷却|炉冷|球磨|渗硅|渗入|热解|固化|除泡|烧结|氧化|"
    r"\b(?:mix|heat|hold|cool|mill|sinter|form|infiltrat)\w*\b",
    re.I,
)


def ranked(
    content: str, expression: str, question: str, limit: int, *, title: str = ""
) -> list[tuple[int, int, str, list[str]]]:
    if not INTENT.search(question):
        return []
    patterns = terms(expression)
    lines = content.splitlines(keepends=True)
    front = re.match(r"\ufeff?---\r?\n.*?\r?\n(?:---|\.\.\.)\s*\r?\n", content, re.S)
    first = content[: front.end()].count("\n") if front else 0
    headings: list[tuple[int, str]] = []
    contexts: dict[int, tuple[str, ...]] = {}
    allowed: set[int] = set()
    fence = ""
    comment = False
    for i, raw in enumerate(lines):
        if i < first:
            continue
        text = raw.strip()
        marker = re.match(r"^(`{3,}|~{3,})", text)
        if marker:
            if not fence:
                fence = marker[1]
            elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = ""
            continue
        if fence:
            continue
        if comment or "<!--" in text:
            comment = "-->" not in text
            continue
        heading = HEADING.match(raw)
        if heading:
            depth = len(heading[1])
            headings = [(level, value) for level, value in headings if level < depth]
            headings.append((depth, heading[2]))
            continue
        path = tuple(value for _, value in headings)
        if sum(map(len, path)) > 600 or any(EXCLUDED.search(value) for value in path):
            continue
        if text.startswith(("!", "[[", "- [[")):
            continue
        contexts[i] = path
        allowed.add(i)

    choices = []
    for start in sorted(allowed):
        # Start at a paragraph or section boundary; never detach a list's
        # temperature/units from its preceding step label.
        if not lines[start].strip() or (start - 1 in allowed and lines[start - 1].strip()):
            continue
        path = contexts[start]
        group = ""
        last_blank = None
        for stop in range(start, len(lines)):
            if stop not in allowed or contexts[stop] != path:
                break
            proposed = group + lines[stop]
            if len(proposed.rstrip("\r\n")) > 400:
                # Do not leave the next step's label without its body or cut
                # its conditions in half at the size boundary.
                group = "".join(lines[start:last_blank]) if last_blank is not None else ""
                break
            group = proposed
            if not lines[stop].strip():
                last_blank = stop
        quote = group.rstrip("\r\n")
        end = start + quote.count("\n")
        actions = len(set(ACTION.findall(quote)))
        if not quote or not actions:
            continue
        context = fold(" ".join((title, *path, quote)))
        coverage = sum(bool(p.search(context)) for p in patterns)
        if not coverage:
            continue
        # Specific step sections outrank principles, then parameter paragraphs.
        # Overview statements and standalone headings cannot become evidence.
        intent = (
            3
            if STEPS.search(" ".join((*path, quote)))
            else 2
            if METHOD.search(" ".join(path))
            else 1
        )
        choices.append(((intent, min(actions, 4), coverage, -start), start, end, quote, path))
    result: list[tuple[int, int, str, list[str]]] = []
    families: set[tuple[str, ...]] = set()
    remaining = sorted(choices, reverse=True)
    # Prefer distinct process sections before adding another window from one.
    for distinct in (True, False):
        for _, start, end, quote, path in remaining:
            family = path[:-1] if len(path) > 1 else path
            if distinct and family in families:
                continue
            if any(not (end + 1 < a or start + 1 > b) for a, b, *_ in result):
                continue
            result.append((start + 1, end + 1, quote, list(path)))
            families.add(family)
            if len(result) == limit:
                return result
    return result
