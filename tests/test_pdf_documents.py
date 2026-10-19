import hashlib
import io
import json
from pathlib import Path

import pytest
from document_support import pdf_bytes

from fiber_bundle.document_contract import DocumentError
from fiber_bundle.pdf_documents import extract
from fiber_bundle.vendor import fake_expert_pdf

pypdf = pytest.importorskip(
    "pypdf", reason="worker-only pinned runtime; run in isolated pypdf context"
)


def test_native_pdf_retains_physical_pages_labels_text_and_original_hash():
    first = "Synthetic ordinary source text contains enough alphabetic characters and value 12."
    source = pdf_bytes((first, ""))
    result = extract(source)
    assert result["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert [p["physical_page"] for p in result["pages"]] == [1, 2]
    assert [p["pdf_page_label"] for p in result["pages"]] == ["i", "ii"]
    assert result["pages"][0]["text"] == first
    assert "raster_image_present" not in result["pages"][0]["review_reasons"]
    assert "numeric_or_unit_review" in result["pages"][0]["review_reasons"]
    assert result["pages_needing_ocr"] == [2]
    assert result["pages"][1]["native_status"] == "missing"
    assert all(p["coordinate_status"] == "unavailable" for p in result["pages"])
    assert not result["publication_ready"] and not result["knowledge_verified"]


def test_image_only_pdf_is_ocr_candidate_not_empty_success():
    result = extract(pdf_bytes(("",), image=True))
    assert result["pages_needing_ocr"] == [1]
    assert result["pages"][0]["text"] == ""
    assert "raster_image_present" in result["pages"][0]["review_reasons"]
    assert result["coverage"]["images_tables_formulas"] == "not_extracted"


def test_low_text_table_equation_and_printed_label_preserve_review():
    text = (
        "Synthetic native evidence with Table 1.1 and equation (2.3) and enough ordinary words 42"
    )
    result = extract(pdf_bytes((text, "tiny")))
    assert {"table_reference_present", "equation_label_present", "numeric_or_unit_review"} <= set(
        result["pages"][0]["review_reasons"]
    )
    assert result["pages"][0]["printed_page_label_candidate"] == "42"
    assert result["pages"][1]["native_status"] == "low"
    assert result["pages_needing_ocr"] == [2]


@pytest.mark.parametrize(
    "source,code",
    [
        (b"not a PDF", "pdf_signature"),
        (b"%PDF-1.4\nbroken", "pdf_parse_failed"),
        (b"", "pdf_source_size"),
    ],
)
def test_invalid_input_has_stable_failure(source, code):
    with pytest.raises(DocumentError, match=code):
        extract(source)


def test_encrypted_pdf_refused_without_password_or_fallback():
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt("synthetic-only")
    stream = io.BytesIO()
    writer.write(stream)
    with pytest.raises(DocumentError, match="pdf_encrypted"):
        extract(stream.getvalue())


def test_page_source_and_text_limits(monkeypatch):
    with pytest.raises(DocumentError, match="pdf_page_limit"):
        extract(pdf_bytes(("",) * 101))
    with pytest.raises(DocumentError, match="pdf_source_size"):
        extract(b"%PDF-" + b"x" * (8 * 1024 * 1024))
    monkeypatch.setattr("fiber_bundle.pdf_documents.MAX_TEXT_BYTES", 20)
    with pytest.raises(DocumentError, match="pdf_text_limit"):
        extract(pdf_bytes())


def test_dependency_pin_checked_before_parsing(monkeypatch):
    monkeypatch.setattr(pypdf, "__version__", "other")
    with pytest.raises(DocumentError, match="pdf_runtime_pin_mismatch"):
        extract(pdf_bytes())


def test_selected_personal_source_is_pinned():
    source = Path(fake_expert_pdf.__file__)
    provenance = json.loads(source.with_name("fake-expert-source.json").read_text())
    assert hashlib.sha256(source.read_bytes()).hexdigest() == provenance["adapted_sha256"]
    assert provenance["pypdf_version"] == "6.10.0"
    assert "main" not in provenance["selected_definitions"]
    assert "not the compiler" in provenance["scope"]


def test_math_font_requires_visual_review():
    result = extract(pdf_bytes(font="CMMI10"))
    assert "math_font_present" in result["pages"][0]["review_reasons"]
    assert result["pages_needing_review"] == [1]
