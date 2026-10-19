"""Bounded local attachment copies and minimal link-target edits for explicit migration."""

import hashlib
import os
import posixpath
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from . import file_exchange
from .config import IntegrityError, contained

WIKI = re.compile(r"!?\[\[([^\]\r\n]+)\]\]")
MARKDOWN = re.compile(
    r"""!?\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|([^()\s]+))(?:\s+["'][^\n]*?["'])?\s*\)"""
)
REFERENCE = re.compile(r"^\s{0,3}\[[^\]\n]+\]:\s*(?:<([^>\n]+)>|(\S+))", re.M)
TAGS = re.compile(r"<(?:a|img|video|audio|source|object)\b[^<>\n]*>", re.I)
ATTRIBUTES = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
MAX_ATTACHMENTS = 128
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024


def mask(text: str) -> str:
    """Keep offsets unchanged while excluding code and comments from link rewrites."""

    def blank(value: str) -> str:
        return "".join(c if c in "\r\n" else " " for c in value)

    text = re.sub(r"<!--.*?-->", lambda m: blank(m[0]), text, flags=re.S)
    lines: list[str] = []
    fence: str | None = None
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            if fence is None:
                fence = marker[1]
            elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = None
            lines.append(blank(line))
        elif fence is not None:
            lines.append(blank(line))
        elif line.startswith("    ") or line.startswith("\t"):
            lines.append(blank(line))
        else:
            lines.append(re.sub(r"`+[^`\n]*`+", lambda m: " " * len(m[0]), line))
    return "".join(lines)


def inbound(inventory: Any, source: str) -> list[dict[str, Any]]:
    """Report incoming note links without editing or reading other notes' attachments."""
    candidates = [n.relative_path for n in inventory.notes]
    result = []
    for note in inventory.notes:
        if note.relative_path == source:
            continue
        visible = mask(note.text)
        for pattern, wiki in ((WIKI, True), (MARKDOWN, False), (REFERENCE, False)):
            for match in pattern.finditer(visible):
                raw = match[1] if match[1] is not None else match[2]
                path = raw.split("|", 1)[0].split("#", 1)[0] if wiki else raw.split("#", 1)[0]
                if not path or urlsplit(path).scheme or path.startswith("//") or "?" in path:
                    continue
                if PurePosixPath(unquote(path)).suffix.lower() not in {"", ".md"}:
                    continue
                try:
                    resolved = resolve(
                        path, inventory.root, note.relative_path, candidates, wiki=wiki
                    )
                except IntegrityError:
                    continue  # Unresolved/ambiguous incoming references cannot be asserted.
                if resolved == source or resolved.removesuffix(".md") == source.removesuffix(".md"):
                    item = {
                        "note_path": note.relative_path,
                        "line": note.text.count("\n", 0, match.start()) + 1,
                        "target": raw,
                    }
                    if item not in result:
                        result.append(item)
    return sorted(result, key=lambda r: (r["note_path"], r["line"], r["target"]))


def paths(root: Path) -> list[str]:
    result, count = [], 0

    def walk(directory: Path, depth: int) -> None:
        nonlocal count
        if depth > 32:
            raise IntegrityError("attachment resolution depth limit exceeded")
        with os.scandir(directory) as entries:
            for entry in entries:
                count += 1
                if count > 10000:
                    raise IntegrityError("attachment resolution entry limit exceeded")
                if entry.name.startswith(".") or entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    walk(Path(entry.path), depth + 1)
                elif entry.is_file(follow_symlinks=False):
                    result.append(Path(entry.path).relative_to(root).as_posix())

    walk(root, 0)
    return sorted(result)


def read(root: Path, relative: str) -> tuple[bytes, list[Any]]:
    path = contained(root, relative)
    parent = file_exchange.parent_fd(root, relative)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or not 0 < info.st_size <= MAX_ATTACHMENT_BYTES
        ):
            raise IntegrityError("attachment is not a bounded independent regular file")
        data = stream.read(MAX_ATTACHMENT_BYTES + 1)
        after = os.fstat(stream.fileno())
        if len(data) != info.st_size or (info.st_size, info.st_mtime_ns) != (
            after.st_size,
            after.st_mtime_ns,
        ):
            raise IntegrityError("attachment changed while reading")
        birth = getattr(info, "st_birthtime", None)
        return data, [info.st_dev, info.st_ino, float(birth if birth is not None else 0).hex()]


def resolve(raw: str, root: Path, source: str, candidates: list[str], *, wiki: bool) -> str:
    decoded = unquote(raw)
    if "\\" in decoded or "\x00" in decoded or any(ord(c) < 32 for c in decoded):
        raise IntegrityError("invalid local migration link")
    local = posixpath.normpath(posixpath.join(posixpath.dirname(source), decoded))
    root_relative = posixpath.normpath(decoded.lstrip("/"))
    if decoded.startswith("/"):
        local = root_relative
    if local.startswith("../") or local == "..":
        raise IntegrityError("migration link escapes source Vault")
    choices = [local]
    if wiki:
        choices.append(root_relative)
    exact = [p for p in set(choices) if p in candidates]
    if not exact and wiki and PurePosixPath(decoded).suffix == "":
        exact = [p for p in candidates if p.lower().endswith(".md") and p[:-3] in choices]
    if not exact and wiki and "/" not in decoded:
        exact = [
            p
            for p in candidates
            if PurePosixPath(p).name == decoded
            or PurePosixPath(p).suffix.lower() == ".md"
            and PurePosixPath(p).stem == decoded
        ]
    if len(exact) > 1:
        raise IntegrityError("ambiguous migration attachment link")
    if exact:
        contained(root, exact[0])
        return exact[0]
    if PurePosixPath(decoded).suffix.lower() not in {"", ".md"}:
        raise IntegrityError("migration attachment missing or unsupported")
    contained(root, local)
    return local


def prepare(
    root: Path,
    source: str,
    target: str,
    identity: str,
    data: bytes,
    *,
    namespace: str | None = None,
) -> tuple[bytes, dict[str, bytes], list[dict[str, Any]], list[str]]:
    text = data.decode("utf-8")
    visible = mask(text)
    if re.search(r"\bsrcset\s*=", visible, re.I):
        raise IntegrityError("migration srcset requires explicit link review")
    candidates = paths(root)
    folder = "fiber-attachments-" + identity.split(":", 1)[1]
    if namespace is not None:
        if not re.fullmatch(r"[0-9a-f]{16}", namespace):
            raise IntegrityError("invalid migration attachment namespace")
        folder += "-" + namespace
    copies: dict[str, bytes] = {}
    evidence: dict[str, dict[str, Any]] = {}
    unresolved: set[str] = set()
    edits = []
    spans: list[tuple[int, int]] = []
    references = []
    supported_markdown = list(MARKDOWN.finditer(visible))
    for opening in re.finditer(r"!?\[[^\]\n]*\]\(", visible):
        if not any(m.start() == opening.start() for m in supported_markdown):
            raise IntegrityError("complex Markdown migration link requires explicit review")
    for pattern, wiki in [(WIKI, True), (MARKDOWN, False), (REFERENCE, False)]:
        for match in pattern.finditer(visible):
            group = 1 if match[1] is not None else 2
            references.append((match.start(group), match.end(group), wiki))
    for tag in TAGS.finditer(visible):
        matched = set()
        for attribute in ATTRIBUTES.finditer(tag[0]):
            if attribute[1].lower() in {"src", "href", "data"}:
                group = 2 if attribute[2] is not None else 3
                references.append(
                    (
                        tag.start() + attribute.start(group),
                        tag.start() + attribute.end(group),
                        False,
                    )
                )
                matched.add(attribute[1].lower())
        if any(
            name.lower() not in matched
            for name in re.findall(r"\b(src|href|data)\s*=", tag[0], re.I)
        ):
            raise IntegrityError("unquoted HTML migration link unsupported")
    for begin, end, wiki in references:
        raw = text[begin:end]
        path = raw.split("|", 1)[0] if wiki else raw
        if "#" in path:
            path = path.split("#", 1)[0]
        if not path or urlsplit(path).scheme or path.startswith("//"):
            continue
        if "?" in path:
            raise IntegrityError("local migration query link unsupported")
        start, stop = begin, begin + len(path)
        if any(start < b and stop > a for a, b in spans):
            continue
        spans.append((start, stop))
        relative = resolve(path, root, source, candidates, wiki=wiki)
        if PurePosixPath(relative).suffix.lower() == ".md" or relative not in candidates:
            unresolved.add(relative)
            continue
        payload, key = read(root, relative)
        sha = hashlib.sha256(payload).hexdigest()
        suffix = PurePosixPath(relative).suffix.lower()
        suffix = suffix if re.fullmatch(r"\.[a-z0-9]{1,16}", suffix) else ".bin"
        name = sha + suffix
        copies[name] = payload
        evidence[relative] = {
            "source_path": relative,
            "file_key": key,
            "sha256": sha,
            "bytes": len(payload),
            "target_name": name,
        }
        if (
            len(evidence) > MAX_ATTACHMENTS
            or sum(len(p) for p in copies.values()) > MAX_TOTAL_BYTES
        ):
            raise IntegrityError("migration attachment scope limit exceeded")
        replacement = (
            posixpath.join(posixpath.dirname(target), folder, name) if wiki else folder + "/" + name
        )
        if not wiki:
            replacement = quote(replacement, safe="/")
        # Replace only the destination path; labels, titles, fragments and all other bytes stay.
        edits.append((start, stop, replacement))
    for start, stop, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[stop:]
    return text.encode("utf-8"), copies, [evidence[k] for k in sorted(evidence)], sorted(unresolved)
