"""Read-only recognition of literal WeChat text exports; ZIP is never a semantic workpack."""

import re
from pathlib import PurePosixPath
from typing import Any

from .archive_members import read
from .config import InvalidInput
from .objects import digest

HEADER = re.compile(
    r"(?m)^(?:\d{4}-\d{2}-\d{2}|\d{4}年\d{1,2}月\d{1,2}日)[ T]\d{1,2}:\d{2}(?::\d{2})?[^\r\n]*$"
)


def inspect(raw: bytes) -> dict[str, Any]:
    members = read(raw)
    paths = {m.path for m in members}
    if "workpack.json" in paths or any(p.startswith(("word/", "xl/", "ppt/")) for p in paths):
        raise InvalidInput("chat_archive_other_purpose")
    text = [m for m in members if m.path.lower().endswith(".txt")]
    if not text or any(m.uncertain for m in members):
        raise InvalidInput("chat_archive_export_unrecognized")
    recognized = []
    for member in text:
        value = member.data.decode("utf-8-sig")
        # An explicit literal export marker or timestamped transcript is required.
        named_export = PurePosixPath(member.path).name == "聊天记录.txt"
        if (
            not HEADER.search(value)
            and not value.startswith(("微信聊天记录", "WeChat Chat History"))
            and not named_export
        ):
            continue  # Other TXT files may be ordinary attachments inside the export.
        if not value.strip():
            raise InvalidInput("chat_archive_export_unrecognized")
        recognized.append(
            {
                "path": member.path,
                "sha256": digest(member.data),
                "timestamp_headers": len(HEADER.findall(value)),
            }
        )
    if not recognized:
        raise InvalidInput("chat_archive_export_unrecognized")
    return {
        "schema": "wechat-text-export-recognition/v1",
        "source_sha256": digest(raw),
        "text_members": recognized,
        "member_count": len(members),
        "engine": "ordinary",
        "chat_identity": "unknown",
        "platform_completeness": "not_verified",
        "semantic_workpack": False,
    }


def require(config: Any, db: Any, row: Any) -> dict[str, Any]:
    from . import operations

    version = db.execute(
        "SELECT source_version_id FROM receipts WHERE job_id=?", (row["receipt_id"],)
    ).fetchone()[0]
    raw = operations.original(
        config, db, row["client_id"], row["vault_id"], version, row["asset_id"], 12 * 1024 * 1024
    )
    return inspect(raw)
