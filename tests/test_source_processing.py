"""Synthetic saved-source admission, extraction, review/publication and platform cancellation."""

import io
import zipfile
from unittest.mock import patch

import pytest
from test_article_tasks import article_scope as article_scope
from test_document_tasks import document_scope as document_scope
from test_execution import policy as policy
from test_feishu_ingress import feishu as feishu
from test_feishu_ingress import freeze, payload
from test_session_search import intake
from test_session_search import owner as owner
from test_weixin_inbox import binding as binding

from fiber_bundle import (
    auth,
    database,
    document_capture,
    document_evidence,
    document_publication,
    document_review,
    document_tasks,
    execution_ledger,
    feishu_receiver,
    literal_documents,
    source_processing_policy,
    source_selection,
    source_tasks,
    weixin_batches,
    weixin_store,
)
from fiber_bundle.config import InvalidInput
from fiber_bundle.document_worker import DocumentCapture
from fiber_bundle.inputs import canonical

WORKERS = {
    "document_image": "sha256:" + "b" * 64,
    "article_image": "sha256:" + "c" * 64,
    "timeout_seconds": 240,
}


def prepare(config, db, policy, cid, vid, receipt, identity="once"):
    settings = source_processing_policy.save(
        config,
        policy,
        WORKERS,
        source_processing_policy.inspect(config, policy)["configuration_sha256"],
    )
    selected = next(
        r for r in source_selection.listing(config, db, cid, vid) if r["receipt_id"] == receipt
    )
    request = dict(
        client_id=cid,
        vault_id=vid,
        receipt_id=receipt,
        asset_id=selected["asset_id"],
        source_sha256=selected["sha256"],
        kind=selected["kind"],
        submission_id=identity,
        configuration_sha256=settings["configuration_sha256"],
        model_configuration_sha256=policy.digest,
    )
    return request, source_tasks.submit(config, db, policy, request)["run_id"]


def worker(config, policy, source, *, guard, task_id, format="pdf", **kwargs):
    guard()
    from fiber_bundle.document_source import DocumentFile

    if isinstance(source, DocumentFile):
        source = source.read()
    root = config.state_root / "document-captures" / task_id
    for name in ("input", "output"):
        (root / name).mkdir(parents=True)
    document_capture.write_new(root / "input", "source.pdf", source)
    from document_support import candidate

    value = (
        candidate(
            source, text="Qingsong pressure 2.4 MPa with enough literal original source text."
        )
        if format == "pdf"
        else literal_documents.extract(source, format)
    )
    document_capture.write_new(root / "output", "result.json", canonical(value).encode())
    guard()
    return DocumentCapture(task_id, root, document_capture.verify(root / "output", source))


def complete(config, db, policy, rid):
    execution_ledger.start(config, db, rid, policy)
    with patch("fiber_bundle.document_worker.execute_document", side_effect=worker):
        document_tasks.extract_bound(config, policy, rid, {}, deadline=float("inf"))
    execution_ledger.transition(db, rid, "succeeded")


@pytest.mark.parametrize("format", ["text", "archive", "pdf"])
def test_text_and_zip_review_publication_and_original_preservation(
    http, owner, config, policy, format
):
    data = "青松\n测试压力 2.4 MPa".encode()
    if format == "archive":
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as zipped:
            zipped.writestr("聊天.txt", "青松\n测试压力 2.4 MPa\n[图片] 图.jpg")
            zipped.writestr("图.jpg", b"\xff\xd8\xffimage")
        data = output.getvalue()
    if format == "pdf":
        from document_support import pdf_bytes

        data = pdf_bytes(("Qingsong pressure 2.4 MPa with enough literal original source text.",))
    cid, _, ids = owner
    db = database.connect(config.state_root)
    auth.set_grant(db, cid, ids["personal"], query=True, original=True, add=True)
    receipt = intake(
        http,
        owner,
        body=data,
        media={"text": "text/plain", "archive": "application/zip", "pdf": "application/pdf"}[
            format
        ],
    )
    manifest = db.execute(
        "SELECT manifest_json FROM receipts WHERE job_id=?", (receipt["job_id"],)
    ).fetchone()[0]
    request, rid = prepare(config, db, policy, cid, ids["personal"], receipt["job_id"])
    assert source_tasks.submit(config, db, policy, request)["run_id"] == rid
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    complete(config, db, policy, rid)
    metadata, candidate = document_evidence.load(config, db, cid, rid)
    assert candidate["format"] == format and candidate.get("complete", True) is True
    units = candidate.get("segments", candidate.get("pages"))
    expected = "Qingsong" if format == "pdf" else "青松"
    assert expected in units[0]["text"]
    with pytest.raises(InvalidInput):
        document_publication.publish_locked(config, db, rid)
    assessment = dict(
        source_sha256=candidate["source_sha256"],
        result_sha256=candidate["result_sha256"],
        source_reviewed=True,
        visual_reviewed=True,
        judgments=[
            dict(unit_id=u["unit_id"], verdict="accept", reason="synthetic original checked")
            for u in document_review.units(candidate)
        ],
    )
    document_review.record(db, rid, candidate, assessment)
    published = document_publication.publish_locked(config, db, rid)
    assert published["state"] == "published"
    assert (
        expected
        in (
            config.project_root / "test vault/temp_vault1" / published["relative_path"] / "note.md"
        ).read_text()
    )
    assert (
        db.execute(
            "SELECT manifest_json FROM receipts WHERE job_id=?", (receipt["job_id"],)
        ).fetchone()[0]
        == manifest
    )
    db.close()


def test_incomplete_zip_and_duplicate_reference_block_publication():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zipped:
        zipped.writestr("聊天.txt", "[图片] 图.jpg")
        zipped.writestr("a/图.jpg", b"\xff\xd8\xffone")
        zipped.writestr("b/图.jpg", b"\xff\xd8\xfftwo")
    candidate = literal_documents.extract(output.getvalue(), "archive")
    assert candidate["complete"] is False
    assert candidate["runtime"]["mapping"]["references"][0]["state"] == "needs_review"


def test_platform_cancel_and_revoke_invalidate_document_candidate(config, feishu, policy):
    feishu_receiver.capture(config, feishu, payload(body={"text": "青松测试压力"}))
    batch = freeze(config, feishu)
    weixin_batches.choose(config, feishu, batch, "save_one")
    receipt = weixin_store.transfer(config, feishu, batch)["receipts"][0]["job_id"]
    db = database.connect(config.state_root)
    auth.set_grant(db, feishu.client_id, feishu.vault_id, query=True, original=True, add=True)
    request, rid = prepare(config, db, policy, feishu.client_id, feishu.vault_id, receipt)
    complete(config, db, policy, rid)
    assert document_evidence.load(config, db, feishu.client_id, rid)[1]["format"] == "text"
    weixin_batches.choose(config, feishu, batch, "cancel")
    with pytest.raises(auth.Denied):
        document_evidence.load(config, db, feishu.client_id, rid)
    with pytest.raises(auth.Denied):
        source_tasks.submit(config, db, policy, request)
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    db.close()


def test_pdf_and_article_source_routes_and_current_review_gate(
    config, policy, document_scope, article_scope
):
    from test_article_publication import completed

    from fiber_bundle import article_reviews, source_task_guard
    from fiber_bundle.article_publication import publish_locked

    cid, vid, parent, _, source = document_scope
    db = database.connect(config.state_root)
    auth.set_grant(db, cid, vid, query=True, original=True, add=True)
    request, rid = prepare(config, db, policy, cid, vid, parent["job_id"])
    assert request["kind"] == "document"
    assert source_selection.kind(source, "bad.bin", "application/octet-stream") == (
        "document",
        "application/pdf",
    )
    assert source_tasks.submit(config, db, policy, request)["run_id"] == rid
    with pytest.raises(InvalidInput, match="conflict"):
        source_tasks.submit(config, db, policy, {**request, "asset_id": "other"})
    with pytest.raises(auth.Denied):
        source_tasks.submit(
            config,
            db,
            policy,
            {
                **request,
                "submission_id": "wrong-vault",
                "vault_id": db.execute("SELECT id FROM vaults WHERE name='supplier'").fetchone()[0],
            },
        )
    source_task_guard.authorize(config, db, execution_ledger.get_run(db, rid))
    db.execute("DELETE FROM source_task_bindings WHERE run_id=?", (rid,))
    with pytest.raises(auth.Denied):
        source_task_guard.authorize(config, db, execution_ledger.get_run(db, rid))
    db.close()

    aid, avid, _ = article_scope
    db = database.connect(config.state_root)
    auth.set_grant(db, aid, avid, query=True, original=True, add=True)
    db.close()
    arid = completed(config, policy, article_scope)
    db = database.connect(config.state_root)
    packet = article_reviews.packet(config, db, arid)
    assert packet["images"] and not packet["review_ready"] and not packet["publication"]
    with pytest.raises(InvalidInput, match="review gate"):
        article_reviews.publish(config, db, arid, "missing", packet["manifest_sha256"])
    assessment = {
        "manifest_sha256": packet["manifest_sha256"],
        "source_reviewed": True,
        "text_reviewed": True,
        "images_reviewed": True,
        "reason": "synthetic checked",
    }
    with pytest.raises(InvalidInput, match="incomplete"):
        article_reviews.record(config, db, arid, {**assessment, "images_reviewed": False})
    first = article_reviews.record(config, db, arid, assessment)
    latest = article_reviews.record(config, db, arid, assessment)
    with pytest.raises(InvalidInput, match="review gate"):
        article_reviews.publish(config, db, arid, first["review_id"], packet["manifest_sha256"])
    value = article_reviews.publish(
        config, db, arid, latest["review_id"], packet["manifest_sha256"]
    )
    path = config.project_root / "test vault/temp_vault1" / value["note_path"]
    assert path.is_file() and path.with_name("date-evidence.json").is_file()
    old = path.read_bytes()
    path.write_bytes(old + b"\nHuman note\n")
    from fiber_bundle.articles import ArticleError
    from fiber_bundle.config import IntegrityError

    with pytest.raises((IntegrityError, ArticleError)):
        publish_locked(config, db, aid, arid)
    assert path.read_bytes().endswith(b"Human note\n")
    auth.set_grant(db, aid, avid, query=True, original=False, add=True)
    with pytest.raises(auth.Denied):
        article_reviews.packet(config, db, arid)
    db.close()


def test_launch_duplicate_and_io_failure_never_retry(http, owner, config, policy, monkeypatch):
    from types import SimpleNamespace

    cid, _, ids = owner
    db = database.connect(config.state_root)
    auth.set_grant(db, cid, ids["personal"], query=True, original=True, add=True)
    parent = intake(http, owner, body="合成资料".encode())
    request, rid = prepare(config, db, policy, cid, ids["personal"], parent["job_id"])
    calls = []

    def launch(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(wait=lambda: 0)

    monkeypatch.setattr(source_tasks.subprocess, "Popen", launch)
    assert source_tasks.launch(config, db, policy, rid)["state"] == "launched"
    assert source_tasks.launch(config, db, policy, rid)["state"] == "launched"
    assert len(calls) == 1 and db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    request["submission_id"] = "new-source"
    other = source_tasks.submit(config, db, policy, request)["run_id"]

    def broken(*args):
        raise OSError("synthetic write failure")

    monkeypatch.setattr(source_tasks, "write_private", broken)
    with pytest.raises(OSError):
        source_tasks.launch(config, db, policy, other)
    assert execution_ledger.get_run(db, other)["state"] == "interrupted"
    assert (
        db.execute(
            "SELECT launch_state FROM source_task_bindings WHERE run_id=?", (other,)
        ).fetchone()[0]
        == "unknown"
    )
    with pytest.raises(InvalidInput):
        source_tasks.launch(config, db, policy, other)
    db.close()
