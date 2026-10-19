"""Opt-in actual fixed worker: larger PDF, complete review, all parts and indexed hits."""

import hashlib
import json
import os
from unittest.mock import patch

import pytest
from document_support import pdf_bytes
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
from fiber_bundle import (
    source_processing_contract as contract,
)
from fiber_bundle.document_source import DocumentFile


@pytest.mark.skipif(
    not os.environ.get("FIBER_DOCUMENT_IMAGE"),
    reason="requires explicit fixed offline worker image",
)
@pytest.mark.parametrize("page_count,padding_mb,repetitions", [(125, 9, 130), (300, 15, 90)])
def test_large_native_pdf_every_physical_page_published_and_searchable(
    http, config, store, owner, page_count, padding_mb, repetitions
):
    # An unreferenced stream increases the ORIGINAL PDF, rather than its extracted text.
    texts = tuple(
        f"physical-chapter-{n:03d} " + "bounded native source words " * repetitions
        for n in range(1, page_count + 1)
    )
    small = pdf_bytes(texts)
    padding = b"P" * (padding_mb * 1024 * 1024)
    old = b"/Length 3 >>\nstream\n\xff\x00\x00\nendstream"
    new = (
        f"/Length {len(padding) + 3} >>\nstream\n".encode()
        + b"\xff\x00\x00"
        + padding
        + b"\nendstream"
    )
    # Rebuild fixture xref offsets after replacing the unused image stream.
    raw = small.replace(old, new)
    delta = len(new) - len(old)
    start = int(small.rsplit(b"startxref\n", 1)[1].splitlines()[0])
    xref = raw[start + delta :]
    lines = xref.splitlines()
    for index, line in enumerate(lines):
        if line.endswith(b" 00000 n ") and int(line[:10]) > small.index(old):
            lines[index] = f"{int(line[:10]) + delta:010d} 00000 n ".encode()
    xref = b"\n".join(lines) + b"\n"
    xref = xref.replace(f"startxref\n{start}\n".encode(), f"startxref\n{start + delta}\n".encode())
    raw = raw[: start + delta] + xref
    assert len(raw) > contract.DEFAULT_LIMITS["source_bytes"]
    db = database.connect(config.state_root)
    cid, _, ids = owner
    vid = ids["personal"]
    value = contract.defaults()
    value.update(
        document_image=os.environ["FIBER_DOCUMENT_IMAGE"],
        timeout_seconds=120,
        oversize_action="split",
    )
    value["limits"] = dict(contract.CAPACITY_LIMITS)
    settings = source_processing_policy.save(config, None, value, None)
    req = request(cid, vid, raw, "synthetic-large.pdf", "process", settings["configuration_sha256"])
    req["overrides"] = {"markdown_bytes": 16384, "pdf_pages": page_count}
    item = upload(config, db, req, raw)
    rid = source_import_execution.prepare(config, db, cid, item["id"])
    db.close()
    with patch.object(DocumentFile, "read", side_effect=AssertionError("whole original read")):
        source_import_execution.execute(config, rid)
        db = database.connect(config.state_root)
        metadata, candidate = document_evidence.load(config, db, cid, rid)
        assert candidate["page_count"] == page_count
        assert [p["physical_page"] for p in candidate["pages"]] == list(range(1, page_count + 1))
        assert [(b["first_page"], b["last_page"]) for b in candidate["batches"]] == [
            (n, min(n + 24, page_count)) for n in range(1, page_count + 1, 25)
        ]
        assert [p["text"] for p in candidate["pages"]] == list(texts)
        document_review.record(db, rid, candidate, assessment(candidate))
        published = document_publication.publish_locked(config, db, rid)
    directory = config.vault_path("test vault/temp_vault1") / published["relative_path"]
    manifest = json.loads((directory / "publication-notes.json").read_bytes())
    assert (
        len(manifest["parts"]) > 1 and manifest["source_sha256"] == hashlib.sha256(raw).hexdigest()
    )
    body = b"".join((directory / part["path"]).read_bytes() for part in manifest["parts"])
    assert hashlib.sha256(body).hexdigest() == manifest["rendered_sha256"]
    assert all(part["byte_count"] <= 16384 for part in manifest["parts"])
    db.close()
    note_search_build.build(config, cid, vid)
    db = database.connect(config.state_root)
    for number in range(1, page_count + 1):
        found = search.search(config, db, cid, f"physical-chapter-{number:03d}", [vid])
        assert found["index_mode"] == "persistent_chunks"
        assert any(h["document_part"]["run_id"] == rid for h in found["results"])
    response = http.get(f"/admin/documents/{rid}/source")
    assert response.status_code == 200 and response.content == raw
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    db.close()
