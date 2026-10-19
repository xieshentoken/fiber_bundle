"""Source-preserving text projections and non-knowledge directory roles."""

import json
import re
from pathlib import PurePosixPath
from typing import Any

ROLE = re.compile(r"^(?:\d{1,3}[-_ ]*)?(?:templates?|模板|模板库|attachments?|附件|assets)$", re.I)
NOISE = re.compile(
    r"^(?:!\[|<img\b|<!--|>\s*(?:来源|原文|抓取时间|整理时间|生成方式|画面主导整理)|"
    r"(?:来源|原文链接|抓取时间|整理时间)\s*[:：]|https?://\S+$)",
    re.I,
)


def excluded(path: str, settings: Any) -> bool:
    parts = PurePosixPath(path).parts
    if any(ROLE.fullmatch(p) for p in parts):
        return True
    for field in ("attachment_root", "original_dir", "image_dir"):
        base = settings.get(field)
        if base and (path == base or path.startswith(base + "/")):
            return True
    return False


def passages(text: str) -> list[dict[str, Any]]:
    """Keep exact offsets, visit the complete source, never substitute cleaned quotes."""
    lines = text.splitlines(keepends=True)
    first = 0
    if lines and lines[0].strip().lstrip("\ufeff") == "---":
        first = next(
            (i + 1 for i, line in enumerate(lines[1:], 1) if line.strip() in {"---", "..."}),
            len(lines),
        )
    result = []
    offset = 0
    fenced = False
    for index, line in enumerate(lines):
        start = offset
        offset += len(line)
        if index < first:
            continue
        value = line.strip()
        if value.startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced or not value or value in {"---", "***"} or NOISE.match(value):
            continue
        # Reference-only image labels and empty template placeholders carry no subject.
        if re.fullmatch(r"\*?\*?图\s*\d+\*?\*?|\{\{[^}]+\}\}|<%.*%>", value):
            continue
        left = len(line) - len(line.lstrip())
        right = len(line.rstrip())
        result.append(
            {
                "start": start + left,
                "end": start + right,
                "text": line[left:right],
                "kind": "heading" if value.startswith("#") else "body",
            }
        )
    return result


def expert(module: Any) -> dict[str, Any]:
    raw = module.get("files", {}).get("manifest.json", b"{}")
    manifest = json.loads(raw) if isinstance(raw, (bytes, str)) else {}
    package = manifest.get("package", {})
    sections = []
    for obj in module["objects"]:
        text = "\n".join(str(obj[k]) for k in ("title", "statement") if obj.get(k))
        if text:
            sections.append({"object_id": obj["id"], "path": obj.get("path"), "text": text})
    return {
        "text": "\n\n".join(s["text"] for s in sections),
        "sections": sections,
        "domain": str(package.get("domain", "")),
        "package_name": str(package.get("name", module.get("title", ""))),
        "concepts": sorted(
            {
                str(t)
                for o in module["objects"]
                for t in o.get("terms", {})
                if str(t).startswith("concept_")
            }
        ),
    }


def moc_profile(moc: Any) -> dict[str, Any]:
    # Generated membership blocks must not feed their own classifier or invalidate it.
    text = re.sub(r"<!-- fiber-moc:.*?<!-- fiber-moc:.*?end -->", "", moc["text"], flags=re.S)
    sections = passages(text)
    # Human member titles and neighbouring MOC links locate notes; they do not
    # define this MOC's own subject and can contaminate semantic routing.
    subjects = [p for p in sections if not re.fullmatch(r"[-*]?\s*\[\[.*\]\]", p["text"])]
    title = moc["title"].strip()
    navigation = bool(re.search(r"总览|导航|目录|overview|knowledge.?base.?index", title, re.I))
    return {
        "id": moc["id"],
        "title": title,
        "path": moc["path"],
        "navigation": navigation,
        "passages": sections,
        "text": "\n".join(p["text"] for p in subjects),
    }


def source_span(body: str, quote: str, *, windows: Any = None) -> dict[str, Any] | None:
    """Match exact characters across presentation whitespace, returning original text.

    Latin word boundaries and all punctuation remain significant. Only whitespace
    adjacent to Chinese text or sentence separators may disappear; no words are edited.
    Skipped metadata, headings and media can never become supporting evidence.
    """
    parts = [p for p in passages(body) if p["kind"] == "body"]

    def covered(start: int, end: int) -> bool:
        return _covers(body, parts, start, end) and (
            windows is None or _covers(body, windows, start, end)
        )

    variants = [quote]
    if quote.startswith(("- ", "* ", "> ")):
        variants.append(quote[2:])
    for candidate in variants:
        start = body.find(candidate)
        while start >= 0:
            end = start + len(candidate)
            if covered(start, end):
                return {"start": start, "end": end, "quote": body[start:end]}
            start = body.find(candidate, start + 1)
    normalized, positions = _normalize(body)
    for candidate in variants:
        needle, _ = _normalize(candidate)
        if not needle:
            continue
        found = normalized.find(needle)
        while found >= 0:
            start, end = positions[found][0], positions[found + len(needle) - 1][1]
            if covered(start, end):
                return {"start": start, "end": end, "quote": body[start:end]}
            found = normalized.find(needle, found + 1)
    return None


def _normalize(text: str) -> tuple[str, list[tuple[int, int]]]:
    chars = []
    positions = []
    presentation = re.compile(r"[\u3400-\u9fff，。！？；：、（）《》「」“”;:!?()]")
    for token in re.finditer(r"\s+|\S", text):
        value = token[0]
        if value.isspace():
            left = text[token.start() - 1 : token.start()]
            right = text[token.end() : token.end() + 1]
            if presentation.search(left + right):
                continue
            value = " "
        chars.append(value)
        positions.append((token.start(), token.end()))
    return "".join(chars), positions


def source_spans(body: str, quote: str, *, windows: Any = None) -> list[dict[str, Any]]:
    """Resolve a quote from the supplied body projection into separate real spans.

    A provider may join body paragraphs around a heading or media. Preserve each
    real fragment separately instead of inventing a continuous original quote.
    No substantive body characters between fragments may be dropped.
    """
    direct = source_span(body, quote, windows=windows)
    if direct:
        return [direct]
    parts = [p for p in passages(body) if p["kind"] == "body"]
    characters: list[str] = []
    locations: list[int | None] = []
    for part in parts:
        if characters:
            characters.append("\n\n")
            locations.extend([None, None])
        characters.append(part["text"])
        locations.extend(range(part["start"], part["end"]))
    projected = "".join(characters)
    normalized, positions = _normalize(projected)
    variants = [quote]
    if quote.startswith(("- ", "* ", "> ")):
        variants.append(quote[2:])
    for candidate in variants:
        needle, _ = _normalize(candidate)
        if not needle:
            continue
        found = normalized.find(needle)
        while found >= 0:
            left, right = positions[found][0], positions[found + len(needle) - 1][1]
            original = [p for p in locations[left:right] if p is not None]
            if not original:
                break
            start, end = original[0], original[-1] + 1
            fragments: list[dict[str, Any]] = []
            for part in parts:
                first, last = max(start, part["start"]), min(end, part["end"])
                if first >= last:
                    continue
                # Every returned fragment must have actually been supplied.
                if windows is not None and not _covers(body, windows, first, last):
                    fragments = []
                    break
                fragments.append({"start": first, "end": last, "quote": body[first:last]})
            if fragments:
                return fragments
            found = normalized.find(needle, found + 1)
    return []


def _covers(body: str, ranges: Any, start: int, end: int) -> bool:
    cursor = start
    for part in ranges:
        if part["end"] <= cursor:
            continue
        if part["start"] >= end:
            break
        if part["start"] > cursor and body[cursor : part["start"]].strip():
            return False
        cursor = max(cursor, part["end"])
        if cursor >= end:
            return True
    return False
