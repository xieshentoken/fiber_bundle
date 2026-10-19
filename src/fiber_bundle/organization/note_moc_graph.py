"""Recognize only verified, journaled moc-only versions of a graph evidence note."""

from dataclasses import replace
from typing import Any


def project(note: Any, source: Any, binding: Any, record: Any) -> Any:
    if note is None or not record or record.get("pending"):
        return note
    if (
        record["path"] != note.relative_path
        or record["after_sha256"] != note.sha256
        or record["after_file_key"] != list(note.file_key or ())
    ):
        return note
    if any(
        entry["sha256"] == source.sha256 and tuple(entry["file_key"]) == binding.file_key
        for entry in record["versions"]
    ):
        return replace(note, sha256=source.sha256, text=source.text, file_key=binding.file_key)
    return note


def baseline(note: Any, record: Any) -> Any:
    if (
        record
        and not record.get("pending")
        and (
            record["path"] == note.relative_path
            and record["after_sha256"] == note.sha256
            and record["after_file_key"] == list(note.file_key or ())
        )
    ):
        return replace(
            note,
            sha256=record["source"]["sha256"],
            file_key=tuple(record["source"]["file_key"]),
            identity=record["source_identity"],
        )
    return note
