"""Literal text/ZIP review candidates with member locators and unknown chat identity."""

from pathlib import PurePosixPath
from typing import Any

from . import archive_members, feishu_archives
from . import material_contract as contract
from .document_contract import MAX_TEXT_BYTES, DocumentError


def extract(source: bytes, format: str) -> dict[str, Any]:
    if format == "text":
        try:
            text = source.decode("utf-8-sig")
        except UnicodeError:
            raise DocumentError("literal_text_encoding") from None
        if not text.strip() or len(text.encode()) > MAX_TEXT_BYTES:
            raise DocumentError("literal_text_size")
        return contract.result(
            source,
            "text",
            [
                contract.segment(
                    text, {"source_part": "source", "paragraph": 1}, ["source_fields_unknown"]
                )
            ],
            runtime={
                "parser": "fiber-literal/v1",
                "chat_identity": "unknown",
                "chat_complete": "unknown",
            },
        )
    if format != "archive":
        raise DocumentError("literal_format")
    members = archive_members.read(source)
    assets = feishu_archives.assets(source, "source.zip")
    descriptors = [dict(a.descriptor(), id=str(index)) for index, a in enumerate(assets)]
    mapping = archive_members.project(source, descriptors)
    units: list[dict[str, Any]] = []
    attachments: list[dict[str, Any]] = []
    complete = True
    known = set()
    for member in members:
        if PurePosixPath(member.path).suffix.lower() == ".txt":
            text = member.data.decode("utf-8-sig")
            if not text.strip():
                complete = False
                continue
            units.append(
                contract.segment(
                    text,
                    {"source_part": f"member:{member.index}:{member.path}", "paragraph": 1},
                    ["source_fields_unknown", "visual_review"],
                )
            )
            known.add(member.index)
        elif feishu_archives.media_type(member.data) in {"image/jpeg", "image/png"}:
            if any(
                a["sha256"]
                == contract.attachment(
                    member.data, feishu_archives.media_type(member.data), "embedded"
                )["sha256"]
                for a in attachments
            ):
                known.add(member.index)
                continue
            attachments.append(
                contract.attachment(
                    member.data, feishu_archives.media_type(member.data), "embedded"
                )
            )
            known.add(member.index)
        else:
            complete = False
    if not units:
        raise DocumentError("archive_text_missing")
    if (
        len(known) != len(members)
        or any(m.uncertain for m in members)
        or any(r["state"] != "resolved" for r in mapping["references"])
    ):
        complete = False
    return contract.result(
        source,
        "archive",
        units,
        attachments=attachments,
        complete=complete,
        runtime={
            "parser": "fiber-literal/v1",
            "mapping": mapping,
            "chat_identity": "unknown",
            "chat_complete": "unknown",
        },
    )
