import json
import os

import pytest
from document_support import candidate

from fiber_bundle import document_capture as capture
from fiber_bundle.document_contract import DocumentError, validate
from fiber_bundle.inputs import canonical

SOURCE = b"%PDF-synthetic-contract-only"


@pytest.mark.parametrize(
    "text",
    [
        "Ordinary original text has enough alphabetic evidence to pass the native threshold.",
        "",
        "tiny",
        "Synthetic measured value １２ has enough characters to require numeric review.",
    ],
)
def test_closed_candidate_preserves_raw_unicode_and_review(text):
    result = candidate(SOURCE, text)
    assert validate(result, SOURCE) == result
    assert result["pages"][0]["text"] == text
    assert result["publication_ready"] is False and result["knowledge_verified"] is False


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r.update(schema=True),
        lambda r: r.update(publication_ready=True),
        lambda r: r.update(knowledge_verified=True),
        lambda r: r.update(unknown="x"),
        lambda r: r.update(source_sha256="a" * 64),
        lambda r: r.update(source_bytes=True),
        lambda r: r.update(page_count=True),
        lambda r: r.update(pages_needing_review=[1]),
        lambda r: r["pages"][0].update(physical_page=True),
        lambda r: r["pages"][0].update(pdf_page_label="bad\nlabel"),
        lambda r: r["pages"][0].update(coordinate_status="verified"),
        lambda r: r["pages"][0].update(dimensions={"width": float("nan"), "height": 792}),
        lambda r: r["pages"][0].update(text="tampered"),
        lambda r: r["pages"][0].update(normalized_sha256="b" * 64),
        lambda r: r["pages"][0].update(review_reasons=["unknown"]),
        lambda r: r["pages"][0].update(native_status="missing"),
    ],
)
def test_result_tampering_is_rejected(change):
    result = candidate(SOURCE)
    change(result)
    with pytest.raises(DocumentError):
        validate(result, SOURCE)


def test_numeric_and_missing_text_cannot_remove_review():
    for text in ("", "Synthetic numeric reading 12 with enough alphabetic supporting characters."):
        result = candidate(SOURCE, text)
        result["pages"][0]["review_reasons"] = []
        result["pages_needing_review"] = []
        with pytest.raises(DocumentError, match="document_review_scope"):
            validate(result, SOURCE)


def test_candidate_verify_binds_source_and_canonical_bytes(tmp_path):
    raw = canonical(candidate(SOURCE)).encode()
    capture.write_new(tmp_path, "result.json", raw)
    result = capture.verify(tmp_path, SOURCE)
    assert len(result["result_sha256"]) == 64
    with pytest.raises(DocumentError):
        capture.verify(tmp_path, b"other original")
    assert (tmp_path / "result.json").read_bytes() == raw


@pytest.mark.parametrize(
    "kind", ["duplicate", "pretty", "invalid", "extra_file", "symlink", "hardlink", "fifo"]
)
def test_invalid_or_unsafe_result_refused(tmp_path, kind):
    target = tmp_path / "result.json"
    raw = canonical(candidate(SOURCE)).encode()
    if kind == "duplicate":
        target.write_bytes(raw[:-1] + b',"schema":1}')
    elif kind == "pretty":
        target.write_text(json.dumps(candidate(SOURCE), indent=2))
    elif kind == "invalid":
        target.write_bytes(b"{")
    elif kind == "extra_file":
        target.write_bytes(raw)
        (tmp_path / "unexpected").write_bytes(b"preserve")
    elif kind == "symlink":
        outside = tmp_path.parent / "outside.json"
        outside.write_bytes(raw)
        target.symlink_to(outside)
    elif kind == "hardlink":
        outside = tmp_path.parent / "outside.json"
        outside.write_bytes(raw)
        os.link(outside, target)
    else:
        os.mkfifo(target)
    with pytest.raises((DocumentError, OSError)):
        capture.verify(tmp_path, SOURCE)
    assert target.exists()


def test_partial_output_and_original_never_overwritten(tmp_path):
    capture.write_new(tmp_path, "source.pdf", SOURCE)
    with pytest.raises(FileExistsError):
        capture.write_new(tmp_path, "source.pdf", b"changed")
    assert capture.read(tmp_path, "source.pdf", 1000) == SOURCE
    for operation in (
        lambda: capture.write_new(tmp_path, "../escape", b"x"),
        lambda: capture.read(tmp_path, "../escape", 1000),
    ):
        with pytest.raises(DocumentError, match="document_file_scope"):
            operation()
