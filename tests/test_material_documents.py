import io
import json
import zipfile
from pathlib import Path

import pytest

from fiber_bundle import chat_documents, document_ocr, material_contract, office_documents
from fiber_bundle.document_contract import DocumentError
from fiber_bundle.inputs import canonical
from fiber_bundle.ocr_documents import dimensions, transcript

FIXTURES = Path(__file__).parent / "fixtures/m8"


@pytest.mark.parametrize("format", ["docx", "xlsx", "pptx"])
def test_valid_office_source_locators_images_and_formula(format):
    source = (FIXTURES / ("sample." + format)).read_bytes()
    value = office_documents.extract(source, format)
    assert value["source_sha256"] == material_contract.sha(source)
    assert value["complete"] and not value["publication_ready"] and not value["knowledge_verified"]
    assert any("12 watts" in s["text"] for s in value["segments"])
    assert any("numeric_or_unit_review" in s["review_reasons"] for s in value["segments"])
    assert material_contract.validate(value, source) == value
    if format == "docx":
        assert any(
            s["locator"] == {"table": 1, "row": 2, "column": 2} and s["text"] == "12 watts"
            for s in value["segments"]
        )
        assert any(
            s["text"] == "Synthetic source header" and "source_part" in s["locator"]
            for s in value["segments"]
        )
        assert value["attachments"]
    elif format == "xlsx":
        formula = next(s for s in value["segments"] if s["locator"]["cell"] == "B2")
        assert formula["locator"]["formula"] == "A2*2" and formula["text"] == ""
        assert "formula_review" in formula["review_reasons"]
        assert all(s["locator"]["merged_ranges"] == ["A3:B3"] for s in value["segments"])
    else:
        assert any(
            s["locator"] == {"slide": 1, "table": 1, "row": 2, "column": 2}
            for s in value["segments"]
        )
        assert value["attachments"]


def rewrite(format, changes):
    original = zipfile.ZipFile(io.BytesIO((FIXTURES / ("sample." + format)).read_bytes()))
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as output:
        for name in original.namelist():
            if name not in changes:
                output.writestr(name, original.read(name))
        for name, raw in changes.items():
            output.writestr(name, raw)
    original.close()
    return data.getvalue()


@pytest.mark.parametrize(
    "name,raw",
    [
        ("a/../outside", b"bad"),
        ("word/vbaProject.bin", b"macro"),
        ("word/vbaproject.bin", b"macro"),
        ("word/document.xml", '<!DOCTYPE a [<!ENTITY x "secret">]><a>&x;</a>'.encode("utf-16")),
        ("word/document.xml", b'<!DOCTYPE a [<!ENTITY x "secret">]><a/>'),
    ],
)
def test_office_unsafe_sources_fail_closed(name, raw):
    with pytest.raises(DocumentError):
        office_documents.extract(rewrite("docx", {name: raw}), "docx")


def test_unsupported_embedded_parts_remain_partial():
    source = rewrite("docx", {"word/embeddings/object.bin": b"original preserved"})
    assert not office_documents.extract(source, "docx")["complete"]
    source = rewrite("docx", {"word/media/unsupported.emf": b"not raster"})
    assert not office_documents.extract(source, "docx")["complete"]


@pytest.mark.parametrize("format", ["docx", "xlsx", "pptx"])
def test_damaged_office_cannot_be_success(format):
    with pytest.raises(DocumentError):
        office_documents.extract(b"not a zip file", format)


def chat(expected=2):
    return canonical(
        {
            "schema": "fiber-chat-source/v1",
            "expected_count": expected,
            "messages": [
                {
                    "text": "Unknown sender says 12 watts",
                    "sender": None,
                    "sent_at": None,
                    "source_message_id": None,
                },
                {
                    "text": "Second original text",
                    "sender": "Provided nickname",
                    "sent_at": "source literal time",
                    "source_message_id": "source-2",
                },
            ],
        }
    ).encode()


@pytest.mark.parametrize("expected,complete", [(None, False), (2, True), (3, False)])
def test_chat_keeps_order_literal_fields_unknowns_and_partial(expected, complete):
    value = chat_documents.extract(chat(expected))
    assert value["complete"] is complete
    first = value["segments"][0]
    assert first["locator"] == {
        "message_index": 1,
        "sender": None,
        "sent_at": None,
        "source_message_id": None,
    }
    assert "source_fields_unknown" in first["review_reasons"]
    assert value["segments"][1]["locator"]["sent_at"] == "source literal time"
    assert value["runtime"]["platform_completeness"] == "not_verified"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda v: v.update(expected_count=1),
        lambda v: v.update(expected_count=True),
        lambda v: v["messages"][0].update(sender=[]),
        lambda v: v["messages"][0].update(extra="invented"),
    ],
)
def test_chat_unknown_shapes_not_silently_normalized(mutate):
    data = json.loads(chat())
    mutate(data)
    with pytest.raises(DocumentError):
        chat_documents.extract(canonical(data).encode())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda v: v["segments"][0].update(locator={"row": 1}),
        lambda v: v["segments"][0].update(text_sha256="0" * 64),
        lambda v: v.update(publication_ready=True),
        lambda v: v["attachments"][0].update(media_type="image/jpeg"),
    ],
)
def test_candidate_host_rejects_forged_locators_hashes_promotion_and_mime(mutate):
    source = (FIXTURES / "sample.docx").read_bytes()
    value = office_documents.extract(source, "docx")
    mutate(value)
    with pytest.raises(DocumentError):
        material_contract.validate(value, source)


def image_candidate():
    source = (FIXTURES / "scan.png").read_bytes()
    width, height = dimensions(source)
    return source, material_contract.result(
        source,
        "image",
        [
            material_contract.segment(
                "12 watts",
                {
                    "physical_page": 1,
                    "width": width,
                    "height": height,
                    "render_sha256": material_contract.sha(source),
                    "boxes": [
                        {"start_char": 0, "end_char": 2, "bbox": [1, 1, 20, 30], "confidence": 0.5}
                    ],
                },
                ["ocr_review", "visual_review"],
            )
        ],
        attachments=[material_contract.attachment(source, "image/png", "page_render")],
        runtime={"backend": "synthetic test only"},
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda v: v["segments"][0]["locator"]["boxes"][0].update(end_char=1000),
        lambda v: v["segments"][0]["locator"]["boxes"][0].update(bbox=[1, 1, 999999, 2]),
        lambda v: v["segments"][0]["locator"]["boxes"][0].update(confidence=float("nan")),
        lambda v: v["attachments"][0].update(role="embedded"),
        lambda v: v["segments"][0]["locator"].update(physical_page=2),
    ],
)
def test_ocr_offsets_boxes_image_hash_role_and_page_order_checked(mutate):
    source, value = image_candidate()
    mutate(value)
    with pytest.raises(DocumentError):
        material_contract.validate(value, source)


def runtime_receipt():
    files = {k: "a" * 64 for k in ["pdftoppm", "tesseract", "eng", "chi_sim", "chi_tra", "worker"]}
    q = {
        "schema": "fiber-ocr-availability/v1",
        "image": "sha256:" + "b" * 64,
        "fixture_sha256": "c" * 64,
        "runtime_files_sha256": files,
        "scope": "synthetic_availability",
        "accuracy_verified": False,
        "attempts": [
            {
                "ordinal": i,
                "state": "passed",
                "text_sha256": "d" * 64,
                "boxes_sha256": "e" * 64,
                "render_sha256": "c" * 64,
            }
            for i in (1, 2, 3)
        ],
    }
    return {
        "schema": "fiber-ocr-runtime/v1",
        "image": q["image"],
        "backend": "tesseract/v1",
        "languages": "eng+chi_sim+chi_tra",
        "qualification": q,
        "qualification_sha256": material_contract.sha(canonical(q).encode()),
        "runtime_files_sha256": files,
        "accuracy_verified": False,
    }


@pytest.mark.parametrize("mutation", ["hash", "model", "attempt", "accuracy"])
def test_ocr_receipt_requires_three_stable_hash_bound_availability_attempts(mutation):
    value = runtime_receipt()
    document_ocr.verify_runtime(value)
    if mutation == "hash":
        value["qualification_sha256"] = "0" * 64
    if mutation == "model":
        value["runtime_files_sha256"]["eng"] = "0" * 64
    if mutation == "attempt":
        value["qualification"]["attempts"][2]["state"] = "failed"
    if mutation == "accuracy":
        value["accuracy_verified"] = True
    with pytest.raises(DocumentError):
        document_ocr.verify_runtime(value)


def test_tsv_preserves_word_positions_and_confidence(tmp_path):
    path = tmp_path / "page.tsv"
    path.write_text(
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n5\t1\t1\t1\t1\t1\t2\t3\t10\t20\t75.0\t12\n5\t1\t1\t1\t1\t2\t20\t3\t20\t20\t50\twatts\n"
    )
    text, boxes = transcript(path)
    assert text == "12 watts" and boxes[1]["start_char"] == 3 and boxes[0]["bbox"] == [2, 3, 12, 23]
    assert boxes[0]["confidence"] == 0.75
