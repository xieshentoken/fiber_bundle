"""Controlled local-link edits using the existing Markdown/code mask."""

import posixpath
import re
from urllib.parse import quote, unquote, urlsplit

from . import note_transfer_links as links
from .config import IntegrityError


def references(text: str) -> list[tuple[int, int, bool, int, int]]:
    visible = links.mask(text)
    if re.search(r"\bsrcset\s*=", visible, re.I):
        raise IntegrityError("layout_link_review_required")
    result = []
    supported = list(links.MARKDOWN.finditer(visible))
    for opening in re.finditer(r"!?\[[^\]\n]*\]\(", visible):
        if not any(m.start() == opening.start() for m in supported):
            raise IntegrityError("layout_link_review_required")
    for pattern, wiki in [(links.WIKI, True), (links.MARKDOWN, False), (links.REFERENCE, False)]:
        for match in pattern.finditer(visible):
            group = 1 if match[1] is not None else 2
            begin, end = match.span(group)
            result.append((begin, end, wiki, match.start(), match.end()))
    for tag in links.TAGS.finditer(visible):
        matched = set()
        for attribute in links.ATTRIBUTES.finditer(tag[0]):
            if attribute[1].lower() in {"src", "href", "data"}:
                group = 2 if attribute[2] is not None else 3
                result.append(
                    (
                        tag.start() + attribute.start(group),
                        tag.start() + attribute.end(group),
                        False,
                        tag.start(),
                        tag.end(),
                    )
                )
                matched.add(attribute[1].lower())
        if any(
            n.lower() not in matched for n in re.findall(r"\b(src|href|data)\s*=", tag[0], re.I)
        ):
            raise IntegrityError("layout_link_review_required")
    return result


def images(
    text: str,
    mapping: dict[str, str],
    entry: str,
    style: str,
    *,
    edits_out: list[tuple[int, int, str]] | None = None,
    logical_entry: str = "note.md",
) -> str:
    edits = []
    for begin, end, wiki, start, stop in references(text):
        raw = text[begin:end]
        path = raw.split("|", 1)[0].split("#", 1)[0] if wiki else raw.split("#", 1)[0]
        decoded = unquote(path).removeprefix("./")
        target = mapping.get(
            posixpath.normpath(posixpath.join(posixpath.dirname(logical_entry), decoded))
        )
        if target is None:
            continue
        replacement = (
            target if wiki else quote(posixpath.relpath(target, posixpath.dirname(entry)), safe="/")
        )
        if style == "obsidian" and text[start:begin].startswith("![") and not wiki:
            label = re.match(r"!\[([^\]]*)\]", text[start:stop])
            caption = label[1] if label else ""
            edits.append((start, stop, "![[" + target + "]]" + (" " + caption if caption else "")))
        else:
            edits.append((begin, begin + len(path), replacement))
    if edits_out is not None:
        edits_out.extend(sorted(set(edits)))
    for start, stop, value in sorted(set(edits), reverse=True):
        text = text[:start] + value + text[stop:]
    return text


def byte_spans(
    text: str,
    edits: list[tuple[int, int, str]],
    units: list[dict[str, object]],
) -> list[dict[str, object]] | None:
    """Project byte boundaries through controlled link edits; ambiguous boundaries stay unknown."""
    changes = [
        (len(text[:begin].encode()), len(text[:end].encode()), len(value.encode()))
        for begin, end, value in sorted(edits)
    ]

    def boundary(offset: object) -> int | None:
        if type(offset) is not int or not 0 <= offset <= len(text.encode()):
            return None
        delta = 0
        for begin, end, size in changes:
            if offset <= begin:
                break
            if offset < end:
                return None
            delta += size - (end - begin)
        return offset + delta

    result = []
    for unit in units:
        start, end = boundary(unit.get("rendered_start")), boundary(unit.get("rendered_end"))
        if start is None or end is None:
            return None
        result.append({**unit, "rendered_start": start, "rendered_end": end})
    return result


def may_reference_moved(text: str, source: str, moves: dict[str, str]) -> bool:
    """Conservative prefilter before parsing unrelated stationary Markdown dialects."""
    import html
    import unicodedata

    visible = (
        unicodedata.normalize("NFC", html.unescape(unquote(text))).replace("\\", "").casefold()
    )
    for path in moves:
        normalized = unicodedata.normalize("NFC", path).casefold()
        relative = posixpath.relpath(normalized, posixpath.dirname(source.casefold()) or ".")
        name = posixpath.basename(normalized)
        if relative in visible or normalized in visible:
            return True
        if name.endswith(".md") and any(
            "[[" + value in visible for value in (name[:-3], normalized[:-3], relative[:-3])
        ):
            return True
    return False


def relocate(
    text: str,
    root: object,
    source: str,
    target: str,
    candidates: list[str],
    moves: dict[str, str],
    *,
    affected_only: bool = False,
) -> tuple[str, list[str]]:
    """Rewrite guarded local links; optionally restrict stationary notes to moved targets."""
    from pathlib import Path

    edits = []
    unresolved = []
    for begin, end, wiki, _, _ in references(text):
        raw = text[begin:end]
        path = raw.split("|", 1)[0].split("#", 1)[0] if wiki else raw.split("#", 1)[0]
        if not path or urlsplit(path).scheme or path.startswith("//"):
            continue
        if affected_only and source == target:
            # Basename matching is intentionally conservative: ambiguous links to a moved
            # name still reach the resolver and block, unrelated historical gaps do not.
            basename = posixpath.basename(unquote(path).split("?", 1)[0]).removesuffix(".md")
            if not any(posixpath.basename(old).removesuffix(".md") == basename for old in moves):
                continue
        if "?" in path:
            unresolved.append(path)
            continue
        try:
            resolved = links.resolve(path, Path(str(root)), source, candidates, wiki=wiki)
        except IntegrityError:
            unresolved.append(path)
            continue
        if resolved not in candidates:
            unresolved.append(path)
            continue
        destination = moves.get(resolved, resolved)
        if wiki:
            replacement = destination
            if not path.endswith(".md") and destination.endswith(".md"):
                replacement = destination[:-3]
        else:
            replacement = quote(posixpath.relpath(destination, posixpath.dirname(target)), safe="/")
        if source != target or destination != resolved:
            edits.append((begin, begin + len(path), replacement))
    for begin, end, value in sorted(set(edits), reverse=True):
        text = text[:begin] + value + text[end:]
    return text, sorted(set(unresolved))
