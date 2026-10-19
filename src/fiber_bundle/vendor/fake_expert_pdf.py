"""Fixed pure native-page inspection subset of the personal fake-expert source.

No compiler, filesystem, OCR, renderer, reviewer or promotion entry points.
Provenance and exact upstream definitions are recorded in fake-expert-source.json.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any


MATH_FONT_PATTERN = re.compile(
    r"(?:CMMI|CMSY|CMEX|MTSY|MTEX|RMTMI|ITALSYMB|SYMBOL|MSAM|MSBM)", re.IGNORECASE
)


EQUATION_LABEL_PATTERN = re.compile(r"\(\s*\d+(?:\.\d+)+\s*\)")


FIGURE_PATTERN = re.compile(r"\b(?:Figure|Fig\.)\s*\d+(?:\.\d+)+", re.IGNORECASE)


TABLE_PATTERN = re.compile(r"\bTable\s*\d+(?:\.\d+)+", re.IGNORECASE)


GLYPH_NAME_PATTERN = re.compile(
    r"/(?:Lambda|Delta|Gamma|Omega|Theta|Phi|Psi|Sigma|alpha|beta|gamma|delta|lambda|omega)"
)


class PdfPipelineError(RuntimeError):
    """Stable user-facing pipeline failure."""


def normalize_text(text: str) -> str:
    """Use the same canonicalization as external anchor verification."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dereference(value: Any) -> Any:
    try:
        return value.get_object()
    except AttributeError:
        return value


def page_fonts(page: Any) -> list[dict[str, Any]]:
    resources = dereference(page.get("/Resources") or {})
    fonts = dereference(resources.get("/Font") or {}) if hasattr(resources, "get") else {}
    output: list[dict[str, Any]] = []
    for key, reference in sorted(fonts.items(), key=lambda row: str(row[0])):
        font = dereference(reference)
        base_font = str(font.get("/BaseFont", "")) if hasattr(font, "get") else ""
        output.append(
            {
                "resource": str(key),
                "base_font": base_font.lstrip("/"),
                "subtype": str(font.get("/Subtype", "")).lstrip("/") if hasattr(font, "get") else "",
                "has_to_unicode": bool(font.get("/ToUnicode")) if hasattr(font, "get") else False,
                "math_font": bool(MATH_FONT_PATTERN.search(base_font)),
            }
        )
    return output


def page_image_count(page: Any) -> int:
    resources = dereference(page.get("/Resources") or {})
    xobjects = dereference(resources.get("/XObject") or {}) if hasattr(resources, "get") else {}
    count = 0
    for reference in xobjects.values():
        obj = dereference(reference)
        if hasattr(obj, "get") and str(obj.get("/Subtype", "")) == "/Image":
            count += 1
    return count


def page_dimensions(page: Any) -> dict[str, float]:
    """Record the physical PDF page size without claiming text coordinates."""
    try:
        media_box = page.mediabox
        width = round(float(media_box.width), 4)
        height = round(float(media_box.height), 4)
    except Exception as error:
        raise PdfPipelineError("pdf_page_dimensions_unavailable") from error
    if width <= 0 or height <= 0:
        raise PdfPipelineError("pdf_page_dimensions_invalid")
    return {"width": width, "height": height}


def _edge_lines(text: str, count: int = 5) -> tuple[list[str], list[str]]:
    lines = [normalize_text(line) for line in text.splitlines() if normalize_text(line)]
    return lines[:count], lines[-count:]


def detect_printed_page_label(text: str) -> str | None:
    """Infer a visible Arabic page number from common header/footer positions."""
    first, last = _edge_lines(text)

    for line in reversed(last):
        if re.fullmatch(r"\d{1,3}", line):
            return line

    for line in first:
        match = re.match(r"^(\d{1,3})\s+\D", line)
        if match:
            return match.group(1)

    for line in first:
        match = re.search(r"\D\s+(\d{1,3})$", line)
        if match:
            return match.group(1)

    for line in last:
        match = re.search(r"\D\s+(\d{1,3})$", line)
        if match:
            return match.group(1)
    return None


def _private_use_present(text: str) -> bool:
    return any("Co" == unicodedata.category(character) for character in text)


def inspect_page(
    page: Any,
    physical_page: int,
    pdf_page_label: str,
    minimum_native_characters: int,
) -> tuple[dict[str, Any], str, str]:
    raw_text = page.extract_text() or ""
    normalized = normalize_text(raw_text)
    non_whitespace = len(re.sub(r"\s", "", raw_text))
    alphanumeric = sum(character.isalnum() for character in normalized)
    if not normalized:
        native_status = "missing"
    elif alphanumeric < minimum_native_characters:
        native_status = "low"
    else:
        native_status = "usable"

    fonts = page_fonts(page)
    math_fonts = sorted({font["base_font"] for font in fonts if font["math_font"]})
    fonts_without_unicode = sorted(
        {font["base_font"] for font in fonts if not font["has_to_unicode"]}
    )
    image_count = page_image_count(page)
    equation_labels = sorted(set(EQUATION_LABEL_PATTERN.findall(normalized)))

    ocr_reasons: list[str] = []
    if native_status == "missing":
        ocr_reasons.append("native_text_missing")
    elif native_status == "low":
        ocr_reasons.append("native_text_below_threshold")

    visual_reasons: list[str] = []
    if native_status != "usable":
        visual_reasons.append("native_text_not_usable")
    if math_fonts:
        visual_reasons.append("math_font_present")
    if equation_labels:
        visual_reasons.append("equation_label_present")
    if GLYPH_NAME_PATTERN.search(raw_text) or "\ufffd" in raw_text or _private_use_present(raw_text):
        visual_reasons.append("suspicious_glyph_decoding")
    if FIGURE_PATTERN.search(normalized):
        visual_reasons.append("figure_reference_present")
    if TABLE_PATTERN.search(normalized):
        visual_reasons.append("table_reference_present")
    if image_count:
        visual_reasons.append("raster_image_present")

    printed_label = detect_printed_page_label(raw_text)
    if native_status == "usable":
        page_role = "text-with-raster" if image_count else "text"
        page_disposition = "compile"
    elif image_count:
        page_role = "image-only-or-scanned"
        page_disposition = "ocr-required"
    else:
        page_role = "blank-or-unusable"
        page_disposition = "manual-review-required"
    page_record = {
        "physical_page": physical_page,
        "pdf_page_label": str(pdf_page_label),
        "printed_page_label_candidate": printed_label,
        "pdf_label_matches_printed_candidate": (
            None if printed_label is None else str(pdf_page_label) == printed_label
        ),
        "native_text": {
            "status": native_status,
            "raw_characters": len(raw_text),
            "normalized_characters": len(normalized),
            "non_whitespace_characters": non_whitespace,
            "alphanumeric_characters": alphanumeric,
            "sha256": sha256_bytes(normalized.encode("utf-8")),
        },
        "font_summary": {
            "font_count": len(fonts),
            "math_fonts": math_fonts,
            "fonts_without_to_unicode": fonts_without_unicode,
        },
        "raster_image_count": image_count,
        "page_role": page_role,
        "page_disposition": page_disposition,
        "equation_labels": equation_labels,
        "ocr_reasons": ocr_reasons,
        "visual_verification_reasons": visual_reasons,
    }
    return page_record, normalized, raw_text
