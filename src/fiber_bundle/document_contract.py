"""Closed, bounded native-PDF candidate contract; physical pages are the source locators."""

import hashlib
import math
import re
import unicodedata
from typing import Any

from .config import IntegrityError
from .document_source import DocumentFile, binding

MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_TEXT_BYTES = 1024 * 1024
MAX_RESULT_BYTES = 4 * 1024 * 1024
OCR_REASONS = {"native_text_missing", "native_text_below_threshold"}
REVIEW_REASONS = {
    "native_text_not_usable",
    "math_font_present",
    "equation_label_present",
    "suspicious_glyph_decoding",
    "figure_reference_present",
    "table_reference_present",
    "raster_image_present",
    "numeric_or_unit_review",
}
FIELDS = {
    "schema",
    "format",
    "source_sha256",
    "source_bytes",
    "parser",
    "page_count",
    "pages",
    "pages_needing_ocr",
    "pages_needing_review",
    "publication_ready",
    "knowledge_verified",
    "coverage",
}
PAGE_FIELDS = {
    "physical_page",
    "pdf_page_label",
    "printed_page_label_candidate",
    "dimensions",
    "coordinate_status",
    "text",
    "text_sha256",
    "normalized_sha256",
    "native_status",
    "ocr_reasons",
    "review_reasons",
}


class DocumentError(ValueError):
    """Stable failure code, without source text, raw exceptions or credentials."""


def reasons(value: Any, allowed: set[str]) -> None:
    if not isinstance(value, list) or any(
        not isinstance(v, str) or v not in allowed for v in value
    ):
        raise DocumentError("document_review_reason")
    if value != sorted(set(value)):
        raise DocumentError("document_review_order")


def validate(value: Any, source: bytes | DocumentFile) -> dict[str, Any]:
    try:
        source_sha, source_size = binding(source, MAX_SOURCE_BYTES)
    except IntegrityError as exc:
        raise DocumentError("document_contract") from exc
    if (
        not isinstance(value, dict)
        or set(value) not in (FIELDS, FIELDS | {"batches"})
        or type(value["schema"]) is not int
        or value["schema"] != 1
        or value["format"] != "pdf"
        or value["publication_ready"] is not False
        or value["knowledge_verified"] is not False
        or value["parser"] != {"id": "fake-expert-native-page", "pypdf": "6.10.0"}
        or value["coverage"]
        != {
            "native_text": "extracted",
            "images_tables_formulas": "not_extracted",
            "ocr": "not_run",
            "visual_review": "not_run",
        }
    ):
        raise DocumentError("document_contract")
    if (
        type(value["source_bytes"]) is not int
        or value["source_bytes"] != source_size
        or value["source_sha256"] != source_sha
    ):
        raise DocumentError("document_source_binding")
    count = value["page_count"]
    if (
        type(count) is not int
        or not 1 <= count <= (300 if "batches" in value else 100)
        or not isinstance(value["pages"], list)
        or len(value["pages"]) != count
    ):
        raise DocumentError("document_page_scope")
    total = 0
    for number, page in enumerate(value["pages"], 1):
        if (
            not isinstance(page, dict)
            or set(page) != PAGE_FIELDS
            or type(page["physical_page"]) is not int
            or page["physical_page"] != number
        ):
            raise DocumentError("document_page_locator")
        for name in ("pdf_page_label", "printed_page_label_candidate"):
            label = page[name]
            if label is None and name == "printed_page_label_candidate":
                continue
            if not isinstance(label, str) or len(label) > 256 or any(ord(c) < 32 for c in label):
                raise DocumentError("document_page_label")
        dims = page["dimensions"]
        if (
            not isinstance(dims, dict)
            or set(dims) != {"width", "height"}
            or any(
                type(n) not in (float, int) or not math.isfinite(n) or n <= 0 for n in dims.values()
            )
            or page["coordinate_status"] != "unavailable"
        ):
            raise DocumentError("document_geometry")
        raw = page["text"]
        if not isinstance(raw, str):
            raise DocumentError("document_text")
        try:
            data = raw.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise DocumentError("document_text") from exc
        total += len(data)
        if total > MAX_TEXT_BYTES:
            raise DocumentError("document_text_limit")
        normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", raw)).strip()
        if (
            page["text_sha256"] != hashlib.sha256(data).hexdigest()
            or page["normalized_sha256"] != hashlib.sha256(normalized.encode()).hexdigest()
        ):
            raise DocumentError("document_text_binding")
        status = (
            "missing"
            if not normalized
            else "low"
            if sum(c.isalnum() for c in normalized) < 40
            else "usable"
        )
        ocr = (
            []
            if status == "usable"
            else ["native_text_missing" if status == "missing" else "native_text_below_threshold"]
        )
        reasons(page["ocr_reasons"], OCR_REASONS)
        reasons(page["review_reasons"], REVIEW_REASONS)
        if (
            page["native_status"] != status
            or page["ocr_reasons"] != ocr
            or (status != "usable" and "native_text_not_usable" not in page["review_reasons"])
            or (
                any(c.isnumeric() for c in raw)
                and "numeric_or_unit_review" not in page["review_reasons"]
            )
        ):
            raise DocumentError("document_review_scope")
    for field, reason_field in (
        ("pages_needing_ocr", "ocr_reasons"),
        ("pages_needing_review", "review_reasons"),
    ):
        expected = [p["physical_page"] for p in value["pages"] if p[reason_field]]
        if (
            not isinstance(value[field], list)
            or any(type(n) is not int for n in value[field])
            or value[field] != expected
        ):
            raise DocumentError("document_review_scope")
    if "batches" in value:
        from .document_capacity import batches

        if value["batches"] != batches(value["pages"]):
            raise DocumentError("document_batch_mapping")
    return dict(value)
