"""Literal chat ZIP to Markdown; attachment references use verified existing Vault copies."""

import json
import posixpath
import re
import unicodedata
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from . import archive_members, chat_archive, feishu_archives, publication_links
from .objects import digest

MARKER = re.compile(r"(?<![!\[])\[(图片|文件|附件|视频|语音|音频)\](?!\()[ \t]*([^\r\n]+)")


def image(data: bytes) -> bool:
    return (
        feishu_archives.media_type(data).startswith("image/")
        or data.startswith((b"GIF87a", b"GIF89a", b"BM"))
        or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
    )


def normalized(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def render(raw: bytes, title: str, paths: dict[str, str]) -> dict[str, Any]:
    recognized = chat_archive.inspect(raw)
    members = archive_members.read(raw)
    transcripts = {item["path"] for item in recognized["text_members"]}
    attachments = [m for m in members if m.path not in transcripts]
    used, gaps, references, missing_images = set(), [], [], []

    def reference(
        literal: str, source: str, caption: str | None = None, *, picture: bool = False
    ) -> str:
        value = unquote(literal.strip().strip('"')).replace("\\", "/")
        candidates = []
        if not urlsplit(value).scheme and not value.startswith("/"):
            locations = {
                normalized(value),
                normalized(
                    posixpath.normpath(posixpath.join(str(PurePosixPath(source).parent), value))
                ),
            }
            candidates = [m for m in attachments if normalized(m.path) in locations]
            if not candidates and "/" not in value:
                candidates = [
                    m
                    for m in attachments
                    if normalized(PurePosixPath(m.path).name) == normalized(value)
                ]
        if not candidates and picture and not urlsplit(value).scheme and not value.startswith("/"):
            missing_images.append(literal)
            references.append(
                {"literal": literal, "state": "missing", "reason": "chat_image_missing"}
            )
            label = re.sub(r"[\[\]\r\n]", " ", literal)
            return "**[图片缺失：" + label + "]**"
        if len(candidates) != 1 or candidates[0].path not in paths:
            reason = (
                "chat_attachment_ambiguous" if len(candidates) > 1 else "chat_attachment_missing"
            )
            gaps.append(reason)
            references.append({"literal": literal, "state": "needs_review", "reason": reason})
            return "[附件待核对：" + literal + "]"
        member = candidates[0]
        target = paths[member.path]
        used.add(member.path)
        references.append(
            {"literal": literal, "member": member.path, "target": target, "state": "resolved"}
        )
        label = re.sub(r"[\[\]|\r\n]", " ", caption or PurePosixPath(member.path).name)
        return "![[" + target + "]]" if image(member.data) else "[[" + target + "|" + label + "]]"

    sections = []
    for member in members:
        if member.path not in transcripts:
            continue
        text = member.data.decode("utf-8-sig")
        edits = [
            (m.start(), m.end(), reference(m[2], member.path, picture=m[1] == "图片"))
            for m in MARKER.finditer(text)
        ]
        for begin, end, wiki, start, stop in publication_links.references(text):
            if any(start < b and stop > a for a, b, _ in edits):
                continue
            literal = text[begin:end].split("|", 1)[0] if wiki else text[begin:end]
            if urlsplit(literal).scheme or literal.startswith("//"):
                continue
            display = text[begin:end].split("|", 1)[1] if wiki and "|" in text[begin:end] else None
            if not wiki:
                match = re.match(r"!?\[([^\]]*)\]", text[start:stop])
                display = match[1] if match else None
            edits.append(
                (
                    start,
                    stop,
                    reference(
                        literal, member.path, display, picture=text[start:stop].startswith("!")
                    ),
                )
            )
        for start, stop, replacement in sorted(edits, reverse=True):
            text = text[:start] + replacement + text[stop:]
        sections.append(("## " + member.path + "\n\n" if len(transcripts) > 1 else "") + text)
    extras = [m for m in attachments if m.path not in used]
    if extras:
        sections.append("## 附件\n\n" + "\n\n".join(reference(m.path, "") for m in extras))
    heading = re.sub(r"[\r\n]", " ", title)
    note = "\n".join(
        [
            "---",
            "title: " + json.dumps(heading, ensure_ascii=False),
            "source_type: wechat_chat_export",
            "source_sha256: " + digest(raw),
            "chat_identity: unknown",
            "platform_completeness: not_verified",
            "knowledge_verified: false",
            *(
                [
                    "completeness_status: partial",
                    "missing_images: "
                    + json.dumps(sorted(set(missing_images)), ensure_ascii=False),
                ]
                if missing_images
                else []
            ),
            "---",
            "# " + heading,
            "",
            *sections,
            "",
        ]
    ).encode()
    return {
        "files": {"note.md": note},
        "source_sha256": digest(raw),
        "result_sha256": digest(note),
        "gaps": sorted(set(gaps)),
        "missing_images": sorted(set(missing_images)),
        "references": references,
        "member_count": len(members),
        "text_members": sorted(transcripts),
    }
