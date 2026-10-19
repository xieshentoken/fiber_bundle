"""Ordinary native parsers publish genuine system reviews."""

from pathlib import Path
from unittest.mock import patch

import pytest
from test_automatic_ingestion import setup
from test_session_search import owner as session_owner  # noqa: F401
from test_source_imports import owner as owner
from test_source_imports import request, upload
from test_source_processing import WORKERS, worker

from fiber_bundle import (
    automatic_ingestion,
    automatic_jobs,
    database,
    search,
    source_import_execution,
    source_processing_policy,
)


def native_worker(config, policy, source, *, guard, task_id, format="pdf", **kwargs):
    if format not in {"docx", "xlsx", "pptx"}:
        return worker(config, policy, source, guard=guard, task_id=task_id, format=format, **kwargs)
    from fiber_bundle import document_capture, office_documents
    from fiber_bundle.document_worker import DocumentCapture
    from fiber_bundle.inputs import canonical

    guard()
    raw = source.read()
    root = config.state_root / "document-captures" / task_id
    for name in ("input", "output"):
        (root / name).mkdir(parents=True)
    document_capture.write_new(root / "input", "source.pdf", raw)
    value = office_documents.extract(raw, format)
    document_capture.write_new(root / "output", "result.json", canonical(value).encode())
    guard()
    return DocumentCapture(task_id, root, document_capture.verify(root / "output", raw))


@pytest.fixture(autouse=True)
def explicit_ticks(monkeypatch):
    monkeypatch.setattr(automatic_ingestion.Runtime, "start", lambda self: None)


@pytest.mark.parametrize("format", ["docx", "xlsx", "pptx", "chat"])
def test_supported_native_sources_publish_without_fake_human_flags(config, owner, format):
    cid, vid = setup(config, owner)
    settings = source_processing_policy.save(config, None, WORKERS, None)
    if format == "chat":
        import io
        import zipfile

        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            z.writestr(
                "chat.txt",
                "微信聊天记录\n2026-01-02 10:30 Alice\nUnification source token 12 watts\n",
            )
        raw = out.getvalue()
        filename = "chat.zip"
    else:
        raw = (Path(__file__).parent / "fixtures/m8" / ("sample." + format)).read_bytes()
        filename = "sample." + format
    with database.connect(config.state_root) as db:
        item = upload(
            config,
            db,
            request(cid, vid, raw, filename, "process", settings["configuration_sha256"]),
            raw,
        )
        jid = item["automatic"]["id"]

    def launch(config, db, client, identity):
        rid = source_import_execution.prepare(config, db, client, identity)
        db.execute("UPDATE source_task_bindings SET launch_state='launched' WHERE run_id=?", (rid,))
        return {}

    with patch("fiber_bundle.source_import_execution.launch", side_effect=launch):
        automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        rid = automatic_jobs.get(db, jid)["value"]["run_id"]
    with patch("fiber_bundle.document_worker.execute_document", side_effect=native_worker):
        source_import_execution.execute(config, rid)
    for _ in range(3):
        automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        job = automatic_jobs.get(db, jid)
        assert job["state"] == "complete", job
        assert db.execute("SELECT count(*) FROM document_reviews").fetchone()[0] == 0
        hits = search.vault_search(config, db, cid, "12 watts", [vid])["results"]
        assert hits and all(h["review_actor"] == "system_rules" for h in hits)
        assert all(h["note_path"].startswith("Inbox/") for h in hits)
        assert all(h["originals"] for h in hits)
