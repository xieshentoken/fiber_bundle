"""Read-only, bounded ZIP metadata and literal reference projection; originals stay immutable."""

import hashlib
import io
import re
import stat
import unicodedata
import zipfile
from dataclasses import dataclass
from itertools import islice
from pathlib import PurePosixPath
from typing import Any

from .config import InvalidInput
from .download_names import filename
from .weixin_events import MAX_BYTES


@dataclass(frozen=True)
class Member:
    index: int
    original_path: str
    path: str
    encoding: str
    uncertain: bool
    data: bytes


def read(data: bytes) -> list[Member]:
    if not data or len(data) > MAX_BYTES:
        raise InvalidInput("invalid ZIP size")
    result = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > 100:
                raise InvalidInput("ZIP contains too many members")
            total = 0
            seen = set()
            for index, info in enumerate(infos):
                original = info.filename
                path, encoding, uncertain = original, "utf-8-flag", False
                if not info.flag_bits & 0x800:
                    encoding, uncertain = "cp437-default", not original.isascii()
                    raw = original.encode("cp437")
                    try:
                        utf8 = raw.decode("utf-8")
                        if utf8.encode("utf-8") == raw:
                            path, encoding, uncertain = utf8, "utf-8-roundtrip-unflagged", False
                    except UnicodeError:
                        pass
                if (
                    info.orig_filename != original
                    or PurePosixPath(path).is_absolute()
                    or "\\" in path
                    or any(p in {".", ".."} or ":" in p for p in path.split("/"))
                    or any(unicodedata.category(c)[0] == "C" for c in path)
                    or original in seen
                    or stat.S_ISLNK(info.external_attr >> 16)
                    or info.flag_bits & 1
                ):
                    raise InvalidInput("unsafe ZIP member")
                seen.add(original)
                if info.is_dir():
                    continue
                total += info.file_size
                if total > MAX_BYTES or info.file_size > 100 * max(info.compress_size, 1):
                    raise InvalidInput("ZIP expansion limit exceeded")
                with archive.open(info) as stream:
                    value = stream.read(info.file_size + 1)
                if len(value) != info.file_size:
                    raise InvalidInput("ZIP member length mismatch")
                if PurePosixPath(path).suffix.lower() == ".txt":
                    value.decode("utf-8-sig")
                result.append(Member(index, original, path, encoding, uncertain, value))
    except (zipfile.BadZipFile, RuntimeError, UnicodeError, OSError, ValueError):
        raise InvalidInput("invalid ZIP archive") from None
    return result


def project(data: bytes, assets: list[dict[str, Any]]) -> dict[str, Any]:
    members = read(data)
    rows: list[dict[str, Any]] = []
    for member in members:
        sha = hashlib.sha256(member.data).hexdigest()
        matches = [
            a
            for a in assets
            if a.get("sha256") == sha
            and a.get("original_name", "").startswith(f"zip-member-{member.index}-")
        ]
        asset = matches[0] if len(matches) == 1 else None
        mime = asset["media_type"] if asset else "application/octet-stream"
        rows.append(
            {
                "member_index": member.index,
                "original_path": member.original_path,
                "decoded_path": member.path,
                "encoding": member.encoding,
                "uncertain": member.uncertain,
                "sha256": sha,
                "asset_id": asset["id"] if asset else None,
                "download_name": filename(
                    f"zip-member-{member.index}-{PurePosixPath(member.path).name}", mime
                ),
            }
        )
    references = []
    for member in members:
        if PurePosixPath(member.path).suffix.lower() != ".txt":
            continue
        text = member.data.decode("utf-8-sig")
        # Only the literal export marker is interpreted, never free-form names guessed from prose.
        for match in islice(re.finditer(r"\[图片\]\s*([^\r\n]+)", text), 100):
            literal = match.group(1).strip()
            normalized = unicodedata.normalize("NFC", literal)
            exact = [
                r for r in rows if unicodedata.normalize("NFC", r["decoded_path"]) == normalized
            ]
            choices = exact or [
                r
                for r in rows
                if unicodedata.normalize("NFC", PurePosixPath(r["decoded_path"]).name) == normalized
            ]
            resolved = (
                len(choices) == 1
                and not choices[0]["uncertain"]
                and choices[0]["asset_id"] is not None
            )
            references.append(
                {
                    "text_member_index": member.index,
                    "literal": literal,
                    "state": "resolved" if resolved else "needs_review",
                    "asset_id": choices[0]["asset_id"] if resolved else None,
                    "download_name": choices[0]["download_name"] if resolved else None,
                }
            )
    return {
        "schema": "zip-member-projection/v1",
        "members": rows,
        "references": references,
        "chat_identity": "unknown",
        "chat_message_count": None,
        "chat_complete": None,
    }
