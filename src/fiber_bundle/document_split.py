"""Lossless UTF-8 Markdown splitting at conservative complete-block boundaries."""

import re
from collections.abc import Iterator

from .config import InvalidInput

MAX_PARTS = 256
CODES = frozenset(
    {
        "document_split_unit_limit",
        "document_split_reference_scope",
        "document_split_part_limit",
        "document_split_total_limit",
    }
)


class SplitRequired(InvalidInput):
    """A retained candidate cannot be safely published under its frozen limits."""

    def __init__(self, code: str):
        if code not in CODES:
            raise ValueError("unknown split failure")
        super().__init__(code)


def blocks(text: str) -> Iterator[str]:
    """Keep frontmatter, fenced/indented code, loose lists and adjacent structures intact.

    Headings stay with the following block. Ambiguous document-wide references/HTML
    are refused by split(), rather than inventing renderer-specific anchor rewrites.
    """
    lines = text.splitlines(keepends=True)
    following_nonblank = [""] * len(lines)
    upcoming = ""
    for index in range(len(lines) - 1, -1, -1):
        following_nonblank[index] = upcoming
        if lines[index].strip():
            upcoming = lines[index]
    current: list[str] = []
    fence: tuple[str, int] | None = None
    frontmatter = False
    list_block = False
    math_block = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if index == 0 and stripped == "---":
            frontmatter = True
        elif frontmatter and line.rstrip("\r\n") in {"---", "..."}:
            frontmatter = False
        elif not frontmatter:
            if fence is None and stripped == "$$":
                math_block = not math_block
            matched = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
            if fence is None and matched:
                fence = (matched[1][0], len(matched[1]))
            elif fence is not None and re.fullmatch(
                r" {0,3}" + re.escape(fence[0]) + "{" + str(fence[1]) + r",}\s*", line
            ):
                fence = None
            if fence is None and re.match(r"^ {0,3}(?:[-+*]|\d+[.)])\s", line):
                list_block = True
        current.append(line)
        if stripped or frontmatter or fence is not None or math_block:
            continue
        following = following_nonblank[index]
        if list_block and (
            not following.strip() or re.match(r"^(?: {2,}|\t| {0,3}(?:[-+*]|\d+[.)])\s)", following)
        ):
            continue
        # A blank inside indented code or a blockquote cannot create a new file.
        previous = next((v for v in reversed(current[:-1]) if v.strip()), "")
        if (re.match(r"^(?: {4,}|\t)", previous) and re.match(r"^(?: {4,}|\t)", following)) or (
            previous.lstrip().startswith(">") and following.lstrip().startswith(">")
        ):
            continue
        if re.match(r"^ {0,3}#{1,6}\s", previous) or (
            re.fullmatch(r" {0,3}(?:=+|-+)\s*", previous)
            and current[0].strip() != "---"
            and sum(bool(v.strip()) for v in current) >= 2
        ):
            continue
        yield "".join(current)
        current = []
        list_block = False
    if current:
        yield "".join(current)


def split(data: bytes, per_file: int, total: int) -> list[bytes]:
    if len(data) > total:
        raise SplitRequired("document_split_total_limit")
    if len(data) <= per_file:
        return [data]
    text = data.decode("utf-8")
    # Fenced code is literal; do not mistake its example links for live link scope.
    visible: list[str] = []
    fence: tuple[str, int] | None = None
    for line in text.splitlines():
        match = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
        if fence is None and match:
            fence = (match[1][0], len(match[1]))
        elif fence is not None and re.fullmatch(
            r"\s*" + re.escape(fence[0]) + "{" + str(fence[1]) + r",}\s*", line
        ):
            fence = None
        elif fence is None:
            visible.append(line)
    if re.search(
        r"(?m)^ {0,3}\[[^\]]+\]:|\]\([^\n)]*#|\]\[[^\]\n]*\]|\[\[#|"
        r"<[/!?A-Za-z]",
        "\n".join(visible),
    ):
        raise SplitRequired("document_split_reference_scope")
    parts: list[bytes] = []
    pending: list[bytes] = []
    pending_bytes = 0
    for block in blocks(text):
        encoded = block.encode()
        if len(encoded) > per_file:
            raise SplitRequired("document_split_unit_limit")
        if pending_bytes + len(encoded) > per_file:
            parts.append(b"".join(pending))
            if len(parts) >= MAX_PARTS:
                raise SplitRequired("document_split_part_limit")
            pending = []
            pending_bytes = 0
        pending.append(encoded)
        pending_bytes += len(encoded)
    if pending:
        parts.append(b"".join(pending))
    if len(parts) > MAX_PARTS:
        raise SplitRequired("document_split_part_limit")
    assert b"".join(parts) == data
    return parts
