"""Bounded, hash-bound Office/OCR/chat candidates; no review authority."""

import base64
import hashlib
import re
from typing import Any

from .document_contract import MAX_RESULT_BYTES, MAX_SOURCE_BYTES, MAX_TEXT_BYTES, DocumentError
from .document_source import DocumentFile, binding
from .inputs import canonical

FORMATS = {"doc", "docx", "xlsx", "pptx", "image", "pdf-ocr", "chat", "text", "archive"}
REASONS = {
    "numeric_or_unit_review",
    "table_review",
    "formula_review",
    "visual_review",
    "ocr_review",
    "missing_text",
    "source_fields_unknown",
}
MIMES = {
    "text/plain": "text",
    "text/markdown": "text",
    "application/zip": "archive",
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "image/png": "image",
    "image/jpeg": "image",
    "image/webp": "image",
    "image/bmp": "image",
    "image/tiff": "image",
    "image/gif": "image",
    "image/heic": "image",
    "image/heif": "image",
    "application/vnd.fiber.chat+json": "chat",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def segment(text: str, locator: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    return {
        "id": "",
        "text": text,
        "text_sha256": sha(text.encode()),
        "locator": locator,
        "review_reasons": sorted(
            set(reasons + (["numeric_or_unit_review"] if any(c.isnumeric() for c in text) else []))
        ),
        "ocr_required": False,
    }


def result(
    source: bytes,
    format: str,
    segments: list[dict[str, Any]],
    *,
    attachments: list[dict[str, Any]] | None = None,
    runtime: dict[str, Any] | None = None,
    complete: bool = True,
) -> dict[str, Any]:
    for i, item in enumerate(segments, 1):
        item["id"] = f"u{i}"
    value = {
        "schema": 2,
        "format": format,
        "source_sha256": sha(source),
        "source_bytes": len(source),
        "segments": segments,
        "attachments": attachments or [],
        "runtime": runtime or {"parser": "fiber-ooxml/v1"},
        "complete": complete,
        "publication_ready": False,
        "knowledge_verified": False,
    }
    return validate(value, source)


def attachment(data: bytes, media_type: str, role: str) -> dict[str, Any]:
    return {
        "sha256": sha(data),
        "media_type": media_type,
        "role": role,
        "data": base64.b64encode(data).decode(),
    }


def validate(value: object, source: bytes | DocumentFile) -> dict[str, Any]:
    source_sha, source_size = binding(source, 8 * 1024 * 1024)

    def fail() -> None:
        raise DocumentError("material_contract_invalid")

    fields = {
        "schema",
        "format",
        "source_sha256",
        "source_bytes",
        "segments",
        "attachments",
        "runtime",
        "complete",
        "publication_ready",
        "knowledge_verified",
    }
    if not isinstance(value, dict) or set(value) != fields:
        fail()
    assert isinstance(value, dict)
    if (
        type(value["schema"]) is not int
        or value["schema"] != 2
        or not isinstance(value["format"], str)
        or value["format"] not in FORMATS
    ):
        fail()
    if (
        not 0 < source_size <= MAX_SOURCE_BYTES
        or type(value["source_bytes"]) is not int
        or (value["source_bytes"], value["source_sha256"]) != (source_size, source_sha)
    ):
        fail()
    if (
        value["publication_ready"] is not False
        or value["knowledge_verified"] is not False
        or type(value["complete"]) is not bool
    ):
        fail()
    units = value["segments"]
    if not isinstance(units, list) or not 1 <= len(units) <= 100:
        fail()
    total = 0
    for i, unit in enumerate(units, 1):
        if (
            not isinstance(unit, dict)
            or set(unit)
            != {"id", "text", "text_sha256", "locator", "review_reasons", "ocr_required"}
            or unit["id"] != f"u{i}"
        ):
            fail()
        text = unit["text"]
        if not isinstance(text, str) or sha(text.encode()) != unit["text_sha256"]:
            fail()
        total += len(text.encode())
        if total > MAX_TEXT_BYTES or type(unit["ocr_required"]) is not bool:
            fail()
        reasons = unit["review_reasons"]
        if (
            not isinstance(reasons, list)
            or any(not isinstance(r, str) or r not in REASONS for r in reasons)
            or reasons != sorted(set(reasons))
        ):
            fail()
        if any(c.isnumeric() for c in text) and "numeric_or_unit_review" not in reasons:
            fail()
        locator = unit["locator"]
        allowed = {
            "docx": {"paragraph", "table", "row", "column", "source_part", "external_link"},
            "xlsx": {
                "paragraph",
                "sheet",
                "cell",
                "formula",
                "cell_type",
                "merged_ranges",
                "source_part",
                "external_link",
            },
            "pptx": {
                "slide",
                "paragraph",
                "table",
                "row",
                "column",
                "source_part",
                "external_link",
            },
            "image": {"physical_page", "render_sha256", "width", "height", "boxes"},
            "pdf-ocr": {
                "physical_page",
                "pdf_page_label",
                "native_text_sha256",
                "render_sha256",
                "width",
                "height",
                "boxes",
            },
            "chat": {"message_index", "sender", "sent_at", "source_message_id"},
            "text": {"source_part", "paragraph"},
            "archive": {"source_part", "paragraph"},
        }["docx" if value["format"] == "doc" else value["format"]]
        if not isinstance(locator, dict) or not locator or not set(locator) <= allowed:
            fail()
        numeric = {
            "paragraph",
            "table",
            "row",
            "column",
            "slide",
            "physical_page",
            "width",
            "height",
            "message_index",
        }
        for key, item in locator.items():
            if key in numeric:
                if type(item) is not int or not 1 <= item <= 100000:
                    fail()
            elif (
                key not in {"boxes", "merged_ranges"}
                and item is not None
                and (not isinstance(item, str) or len(item) > 2048)
            ):
                fail()
        format = "docx" if value["format"] == "doc" else value["format"]
        external = set(locator) == {"source_part", "external_link"}
        auxiliary = set(locator) == {"source_part", "paragraph"}
        valid_locator = {
            "text": set(locator) == {"source_part", "paragraph"},
            "archive": set(locator) == {"source_part", "paragraph"},
            "docx": external
            or auxiliary
            or set(locator) == {"paragraph"}
            or set(locator) == {"table", "row", "column"},
            "xlsx": external
            or auxiliary
            or set(locator) == {"sheet", "cell", "formula", "cell_type", "merged_ranges"},
            "pptx": external
            or auxiliary
            or set(locator) == {"slide", "paragraph"}
            or set(locator) == {"slide", "table", "row", "column"}
            or set(locator) == {"slide"},
            "chat": set(locator) == {"message_index", "sender", "sent_at", "source_message_id"},
            "image": set(locator) == {"physical_page", "render_sha256", "width", "height", "boxes"},
            "pdf-ocr": set(locator)
            == {
                "physical_page",
                "pdf_page_label",
                "native_text_sha256",
                "render_sha256",
                "width",
                "height",
                "boxes",
            },
        }[format]
        if not valid_locator:
            fail()
        if (
            format == "xlsx"
            and "cell" in locator
            and not re.fullmatch(r"[A-Z]+[1-9][0-9]*", locator["cell"])
        ):
            fail()
        if format == "chat" and locator["message_index"] != i:
            fail()
        if format == "pdf-ocr" and not re.fullmatch(
            "[0-9a-f]{64}", locator["native_text_sha256"] or ""
        ):
            fail()
        if value["format"] in {"image", "pdf-ocr"}:
            from .material_geometry import validate_boxes

            validate_boxes(unit)
        if (
            value["format"] == "xlsx"
            and "cell" in locator
            and (
                not isinstance(locator.get("merged_ranges"), list)
                or any(
                    not isinstance(n, str)
                    or not re.fullmatch(r"[A-Z]+[1-9][0-9]*:[A-Z]+[1-9][0-9]*", n)
                    for n in locator["merged_ranges"]
                )
            )
        ):
            fail()
    images = value["attachments"]
    if not isinstance(images, list) or len(images) > 100:
        fail()
    seen = set()
    renders = set()
    for item in images:
        if (
            not isinstance(item, dict)
            or set(item) != {"sha256", "media_type", "role", "data"}
            or not isinstance(item["media_type"], str)
            or item["media_type"] not in {"image/png", "image/jpeg"}
            or not isinstance(item["role"], str)
            or item["role"] not in {"embedded", "page_render"}
            or not isinstance(item["data"], str)
        ):
            fail()
        try:
            raw = base64.b64decode(item["data"], validate=True)
        except ValueError:
            fail()
        if (
            not raw
            or len(raw) > MAX_SOURCE_BYTES
            or sha(raw) != item["sha256"]
            or item["sha256"] in seen
        ):
            fail()
        if not raw.startswith(
            b"\x89PNG\r\n\x1a\n" if item["media_type"] == "image/png" else b"\xff\xd8\xff"
        ):
            fail()
        if item["role"] == "page_render":
            renders.add(item["sha256"])
        seen.add(item["sha256"])
    if value["format"] in {"image", "pdf-ocr"}:
        for i, unit in enumerate(units, 1):
            if (
                unit["locator"]["render_sha256"] not in renders
                or unit["locator"]["physical_page"] != i
            ):
                fail()
    if (
        not isinstance(value["runtime"], dict)
        or not value["runtime"]
        or len(canonical(value["runtime"])) > 16384
    ):
        fail()
    if len(canonical(value).encode()) > MAX_RESULT_BYTES:
        fail()
    if value["format"] == "doc":
        from .document_conversion import verify_mapping, verify_runtime

        runtime = {k: v for k, v in value["runtime"].items() if k != "conversion"}
        verify_runtime(runtime)
        verify_mapping(value, runtime)
    return value
