"""Worker-only native PDF extraction using the pinned personal fake-expert page rules."""

import hashlib
import importlib
import io
import math
from collections.abc import Callable
from typing import Any, BinaryIO

from .config import IntegrityError
from .document_capacity import BATCH_PAGES, batches, checked
from .document_contract import MAX_TEXT_BYTES, DocumentError, validate
from .document_source import DocumentFile, binding
from .vendor import fake_expert_pdf as upstream

PINNED_PYPDF = "6.10.0"
MAX_PAGES = 100
MINIMUM_NATIVE_CHARACTERS = 40


def extract(
    source: bytes | DocumentFile,
    limits: dict[str, int] | None = None,
    *,
    on_batch: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """No network, OCR, layout fallback, publication or reviewer authority."""
    effective = checked(limits)
    try:
        source_sha, source_size = binding(source, effective["source_bytes"])
    except IntegrityError as exc:
        raise DocumentError("pdf_source_size") from exc
    if isinstance(source, DocumentFile):
        with source.open() as stream:
            return _extract(
                stream, source_sha, source_size, source, effective, limits is not None, on_batch
            )
    return _extract(
        io.BytesIO(source), source_sha, source_size, source, effective, limits is not None, on_batch
    )


def _extract(
    stream: BinaryIO,
    source_sha: str,
    source_size: int,
    source: bytes | DocumentFile,
    limits: dict[str, int],
    mapped: bool,
    on_batch: Callable[[dict[str, Any]], None] | None,
) -> dict[str, Any]:
    if stream.read(5) != b"%PDF-":
        raise DocumentError("pdf_signature")
    stream.seek(0)
    try:
        pypdf = importlib.import_module("pypdf")
    except ImportError as exc:
        raise DocumentError("pdf_runtime_unavailable") from exc
    if pypdf.__version__ != PINNED_PYPDF:
        raise DocumentError("pdf_runtime_pin_mismatch")
    try:
        reader = pypdf.PdfReader(stream, strict=True)
        if reader.is_encrypted:
            raise DocumentError("pdf_encrypted")
        count = len(reader.pages)
        if not 1 <= count <= limits["pdf_pages"]:
            raise DocumentError("pdf_page_limit")
        labels = reader.page_labels
        if len(labels) != count:
            raise DocumentError("pdf_page_labels")
        pages = []
        total = 0
        for index in range(count):
            number = index + 1
            page = reader.pages[index]
            record, normalized, raw = upstream.inspect_page(
                page, number, labels[number - 1], MINIMUM_NATIVE_CHARACTERS
            )
            total += len(raw.encode("utf-8"))
            if total > min(MAX_TEXT_BYTES, limits["text_bytes"]):
                raise DocumentError("pdf_text_limit")
            dimensions = upstream.page_dimensions(page)
            if any(not math.isfinite(n) for n in dimensions.values()):
                raise DocumentError("pdf_dimensions")
            review = record["visual_verification_reasons"].copy()
            if any(c.isnumeric() for c in raw):
                review.append("numeric_or_unit_review")
            pages.append(
                {
                    "physical_page": number,
                    "pdf_page_label": labels[number - 1],
                    "printed_page_label_candidate": record["printed_page_label_candidate"],
                    "dimensions": dimensions,
                    "coordinate_status": "unavailable",
                    "text": raw,
                    "text_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                    "normalized_sha256": upstream.sha256_bytes(normalized.encode()),
                    "native_status": record["native_text"]["status"],
                    "ocr_reasons": record["ocr_reasons"],
                    "review_reasons": sorted(set(review)),
                }
            )
            if on_batch is not None and (number % BATCH_PAGES == 0 or number == count):
                start = ((number - 1) // BATCH_PAGES) * BATCH_PAGES
                on_batch(
                    {
                        "schema": "pdf-page-batch/v1",
                        "source_sha256": source_sha,
                        "source_bytes": source_size,
                        "first_page": start + 1,
                        "last_page": number,
                        "pages": pages[start:number],
                    }
                )
        result = {
            "schema": 1,
            "format": "pdf",
            "source_sha256": source_sha,
            "source_bytes": source_size,
            "parser": {"id": "fake-expert-native-page", "pypdf": PINNED_PYPDF},
            "page_count": count,
            "pages": pages,
            "pages_needing_ocr": [p["physical_page"] for p in pages if p["ocr_reasons"]],
            "pages_needing_review": [p["physical_page"] for p in pages if p["review_reasons"]],
            "publication_ready": False,
            "knowledge_verified": False,
            "coverage": {
                "native_text": "extracted",
                "images_tables_formulas": "not_extracted",
                "ocr": "not_run",
                "visual_review": "not_run",
            },
        }
        if mapped:
            result["batches"] = batches(pages)
        validate(result, source)
        return result
    except DocumentError:
        raise
    except Exception as exc:
        # Third-party PDF failures are varied and must not leak input into tool logs.
        raise DocumentError("pdf_parse_failed") from exc
