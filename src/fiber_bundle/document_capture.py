"""Private candidate files and host contract verification; no PDF parsing on the host."""

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from .config import sync_directory
from .document_contract import MAX_RESULT_BYTES, DocumentError, validate
from .document_source import DocumentFile
from .inputs import canonical


def read(root: Path, name: str, maximum: int) -> bytes:
    if (
        name not in {"source.pdf", "result.json", "converted.docx"}
        and re.fullmatch(r"batch-[0-9]{4}\.json", name) is None
    ):
        raise DocumentError("document_file_scope")
    fd = os.open(root / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 0 < before.st_size <= maximum
        ):
            raise DocumentError("document_file_type_or_size")
        data = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
        if len(data) != before.st_size or (before.st_size, before.st_mtime_ns) != (
            after.st_size,
            after.st_mtime_ns,
        ):
            raise DocumentError("document_file_changed")
        return data


def write_new(root: Path, name: str, data: bytes) -> None:
    if (
        name not in {"source.pdf", "result.json", "request.json", "converted.docx"}
        and re.fullmatch(r"batch-[0-9]{4}\.json", name) is None
    ):
        raise DocumentError("document_file_scope")
    fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(root)


def capture(
    input_root: Path,
    output_root: Path,
    format: str = "pdf",
    runtime: dict[str, Any] | None = None,
    limits: dict[str, int] | None = None,
) -> None:
    """Fixed worker invokes this once; incomplete output is never a successful result."""
    from .document_capacity import checked
    from .pdf_documents import extract

    effective = checked(limits)

    for root in (input_root, output_root):
        if root.is_symlink() or root.resolve() != root or not root.is_dir():
            raise DocumentError("document_directory")
    if sorted(p.name for p in input_root.iterdir()) != ["source.pdf"] or any(output_root.iterdir()):
        raise DocumentError("document_capture_scope")
    if format == "pdf" and runtime is None:
        path = input_root / "source.pdf"
        # The worker binds the exact mounted bytes before parsing them through a file stream.
        size = path.stat(follow_symlinks=False).st_size
        if not 0 < size <= effective["source_bytes"]:
            raise DocumentError("document_source_size")
        digest = hashlib.sha256()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            while chunk := stream.read(256 * 1024):
                digest.update(chunk)
        file = DocumentFile(path, digest.hexdigest(), size)

        def save_batch(batch: dict[str, Any]) -> None:
            from .document_capacity import BATCH_PAGES

            ordinal = (batch["first_page"] - 1) // BATCH_PAGES + 1
            write_new(output_root, f"batch-{ordinal:04d}.json", canonical(batch).encode())

        value = extract(file, limits, on_batch=save_batch if limits is not None else None)
        result = canonical(value).encode()
        if len(result) > effective["result_bytes"]:
            raise DocumentError("document_result_limit")
        write_new(output_root, "result.json", result)
        return
    source = read(input_root, "source.pdf", 8 * 1024 * 1024)
    if format == "doc" and runtime is not None:
        from .office_conversion import extract as extract_doc

        value, converted = extract_doc(source, runtime, Path("/tmp"))
        write_new(output_root, "converted.docx", converted)
    elif runtime is not None:
        from .ocr_documents import extract as extract_ocr

        value = extract_ocr(source, format, runtime, input_root / "source.pdf", Path("/tmp"))
    elif format in {"docx", "xlsx", "pptx"}:
        from .office_documents import extract as extract_office

        value = extract_office(source, format)
    elif format in {"text", "archive"}:
        from .literal_documents import extract as extract_literal

        value = extract_literal(source, format)
    elif format == "chat":
        from .chat_documents import extract as extract_chat

        value = extract_chat(source)
    elif format == "pdf":
        value = extract(source)
    else:
        raise DocumentError("document_format")
    result = canonical(value).encode()
    if len(result) > MAX_RESULT_BYTES:
        raise DocumentError("document_result_limit")
    write_new(output_root, "result.json", result)


def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DocumentError("document_duplicate_field")
        value[key] = item
    return value


def verify(root: Path, source: bytes | DocumentFile) -> dict[str, Any]:
    if root.is_symlink() or root.resolve() != root or not root.is_dir():
        raise DocumentError("document_result_scope")
    try:
        raw = read(root, "result.json", MAX_RESULT_BYTES)
        value = json.loads(raw, object_pairs_hook=reject_duplicate)
        if isinstance(value, dict) and value.get("schema") == 2:
            from .material_contract import validate as validate_material

            result = validate_material(value, source)
        else:
            result = validate(value, source)
        expected_files = {"result.json"}
        if result.get("format") == "doc":
            expected_files.add("converted.docx")
            converted = read(root, "converted.docx", 8 * 1024 * 1024)
            mapping = result["runtime"]["conversion"]
            if (hashlib.sha256(converted).hexdigest(), len(converted)) != (
                mapping["derived_sha256"],
                mapping["derived_bytes"],
            ):
                raise DocumentError("document_conversion_binding")
        if "batches" in result:
            for ordinal, batch in enumerate(result["batches"], 1):
                name = f"batch-{ordinal:04d}.json"
                expected_files.add(name)
                expected = {
                    "schema": "pdf-page-batch/v1",
                    "source_sha256": result["source_sha256"],
                    "source_bytes": result["source_bytes"],
                    "first_page": batch["first_page"],
                    "last_page": batch["last_page"],
                    "pages": result["pages"][batch["first_page"] - 1 : batch["last_page"]],
                }
                if read(root, name, MAX_RESULT_BYTES) != canonical(expected).encode():
                    raise DocumentError("document_batch_binding")
        if {path.name for path in root.iterdir()} != expected_files:
            raise DocumentError("document_result_scope")
        if canonical(result).encode() != raw:
            raise DocumentError("document_result_not_canonical")
        return {**result, "result_sha256": hashlib.sha256(raw).hexdigest()}
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, DocumentError):
            raise
        raise DocumentError("document_result_invalid") from exc
