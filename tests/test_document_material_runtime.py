"""Opt-in actual offline parsers and OCR/conversion through the import/review ledger."""

import json
import os
from pathlib import Path

import pytest
from test_document_human_publication import assessment
from test_session_search import owner as session_owner  # noqa: F401
from test_source_imports import owner as owner
from test_source_imports import request, upload

from fiber_bundle import (
    database,
    document_evidence,
    document_publication,
    document_review,
    note_search_build,
    search,
    source_import_execution,
    source_processing_policy,
)
from fiber_bundle import source_processing_contract as contract
from fiber_bundle.document_contract import DocumentError

ROOT = Path(os.environ.get("FIBER_MATERIAL_FIXTURES", "/missing/material-fixtures"))
pytestmark = pytest.mark.skipif(
    not os.environ.get("FIBER_MATERIAL_IMAGE"),
    reason="requires explicit fixed offline runtime and synthetic fixtures",
)


def settings(config):
    value = {
        **contract.defaults(),
        "schema": 3,
        "document_image": os.environ["FIBER_MATERIAL_IMAGE"],
        "timeout_seconds": 180,
        "ocr_runtime": json.loads((ROOT / "ocr-runtime.json").read_bytes()),
        "conversion_runtime": json.loads((ROOT / "conversion-runtime.json").read_bytes()),
    }
    return source_processing_policy.save(config, None, value, None)


@pytest.mark.parametrize(
    "filename,profile,count",
    [
        ("scan.png", "ocr", 1),
        ("output/sample.jpg", "ocr", 1),
        ("output/sample.webp", "ocr", 1),
        ("output/sample.bmp", "ocr", 1),
        ("output/sample.gif", "ocr", 1),
        ("output/sample.heic", "ocr", 1),
        ("output/sample.heif", "ocr", 1),
        ("output/sample.tiff", "ocr", 1),
        ("output/multi.tiff", "ocr", 2),
        ("output/source.doc", "conversion", None),
        ("sample.xlsx", "native", None),
        ("sample.pptx", "native", None),
    ],
)
def test_actual_format_full_import_review_publish_query(
    config, store, owner, filename, profile, count
):
    cid, _, ids = owner
    vid = ids["personal"]
    policy = settings(config)
    path = (
        ROOT / filename
        if filename not in {"sample.xlsx", "sample.pptx"}
        else Path(__file__).parent / "fixtures/m8" / filename
    )
    raw = path.read_bytes()
    req = request(cid, vid, raw, path.name, "process", policy["configuration_sha256"])
    req["profile"] = profile
    db = database.connect(config.state_root)
    item = upload(config, db, req, raw)
    rid = source_import_execution.prepare(config, db, cid, item["id"])
    db.close()
    source_import_execution.execute(config, rid)
    db = database.connect(config.state_root)
    metadata, candidate = document_evidence.load(config, db, cid, rid)
    assert candidate["complete"] and candidate["knowledge_verified"] is False
    if count:
        assert len(candidate["segments"]) == count
        assert [s["locator"]["physical_page"] for s in candidate["segments"]] == list(
            range(1, count + 1)
        )
        assert all(s["locator"]["boxes"] for s in candidate["segments"])
    assert any("12" in s["text"] for s in candidate["segments"])
    if profile == "conversion":
        assert candidate["format"] == "doc" and candidate["attachments"]
        assert any("table" in s["locator"] for s in candidate["segments"])
        assert metadata["runtime"]["conversion"]["source_sha256"] == item["source_sha256"]
    document_review.record(db, rid, candidate, assessment(candidate))
    publication = document_publication.publish_locked(config, db, rid)
    assert publication["state"] == "published"
    db.close()
    note_search_build.build(config, cid, vid)
    db = database.connect(config.state_root)
    hits = search.search(config, db, cid, "12", [vid])
    assert hits["results"] and hits["index_mode"] == "persistent_chunks"
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    db.close()


@pytest.mark.parametrize(
    "name,raw",
    [
        ("broken.webp", b"RIFFbrokenWEBP"),
        ("broken.png", b"\x89PNG\r\n\x1a\ninvalid"),
        ("broken.jpg", b"\xff\xd8\xffinvalid"),
        ("broken.bmp", b"BMinvalid"),
        ("broken.gif", b"GIF89ainvalid"),
        ("broken.tiff", b"II*\x00invalid"),
        ("broken.heic", b"\x00\x00\x00\x18ftypheicinvalid"),
        ("broken.heif", b"\x00\x00\x00\x18ftypmif1invalid"),
        ("broken.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1bad"),
        ("broken.xlsx", b"not zip"),
        ("broken.pptx", b"not zip"),
        ("too-wide.png", None),
        ("encrypted.doc", None),
    ],
)
def test_actual_failure_keeps_original_and_never_publishes(config, store, owner, name, raw):
    cid, _, ids = owner
    policy = settings(config)
    if raw is None:
        raw = (ROOT / ("output/" + name)).read_bytes()
    req = request(cid, ids["personal"], raw, name, "process", policy["configuration_sha256"])
    req["profile"] = (
        "ocr"
        if name.endswith(("webp", "png", "jpg", "bmp", "gif", "tiff", "heic", "heif"))
        else "conversion"
        if name.endswith("doc")
        else "native"
    )
    db = database.connect(config.state_root)
    item = upload(config, db, req, raw)
    rid = source_import_execution.prepare(config, db, cid, item["id"])
    db.close()
    with pytest.raises(DocumentError, match="document_worker_failed"):
        source_import_execution.execute(config, rid)
    db = database.connect(config.state_root)
    assert (
        db.execute("SELECT state FROM execution_runs WHERE id=?", (rid,)).fetchone()[0] == "failed"
    )
    assert db.execute("SELECT count(*) FROM document_publications").fetchone()[0] == 0
    assert (
        db.execute("SELECT state FROM receipts WHERE job_id=?", (item["receipt_id"],)).fetchone()[0]
        == "stored"
    )
    db.close()
