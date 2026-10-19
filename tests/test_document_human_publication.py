import base64
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from test_document_evidence import completed as completed
from test_document_evidence import document_scope as document_scope
from test_document_tasks import call
from test_execution import policy as policy
from test_material_documents import FIXTURES, chat

from fiber_bundle import (
    auth,
    database,
    document_bindings,
    document_capture,
    document_evidence,
    document_publication,
    document_review,
    document_worker,
    execution_ledger,
    office_documents,
    publication_files,
    search,
    vaults,
)
from fiber_bundle.api import create_app
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.document_worker import DocumentCapture, DocumentPolicy
from fiber_bundle.inputs import canonical
from fiber_bundle.operations import intake
from fiber_bundle.payloads import Intake


def allow(config, scope, *, original=True, add=True):
    db = database.connect(config.state_root)
    auth.set_grant(db, scope[0], scope[1], query=True, original=original, add=add)
    return db


def assessment(candidate, verdict="accept"):
    return {
        "source_sha256": candidate["source_sha256"],
        "result_sha256": candidate["result_sha256"],
        "source_reviewed": True,
        "visual_reviewed": True,
        "judgments": [
            {
                "unit_id": u["unit_id"],
                "verdict": verdict,
                "reason": "Synthetic fixture assertions reviewed; test only, no accuracy claim",
            }
            for u in document_review.units(candidate)
        ],
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda v: v.update(source_sha256="0" * 64),
        lambda v: v.update(result_sha256="0" * 64),
        lambda v: v.update(source_reviewed=False),
        lambda v: v["judgments"].pop(),
        lambda v: v["judgments"][0].update(reason=" "),
        lambda v: v["judgments"].append(v["judgments"][0]),
    ],
)
def test_review_bound_all_units_nonblank_explicit_human_flags(
    config, document_scope, completed, http, mutate
):
    db = allow(config, document_scope)
    _, c = document_evidence.load(config, db, document_scope[0], completed)
    v = assessment(c)
    mutate(v)
    db.close()
    assert http.post(f"/admin/documents/{completed}/reviews", json=v).status_code == 422
    assert http.post(f"/admin/documents/{completed}/publish").status_code == 422
    db = database.connect(config.state_root)
    assert db.execute("SELECT count(*) FROM document_reviews").fetchone()[0] == 0
    db.close()


@pytest.mark.parametrize(
    "verdict,ready", [("accept", True), ("reject", False), ("uncertain", False)]
)
def test_review_no_model_and_gate_requires_actual_acceptance(
    config, document_scope, completed, http, verdict, ready
):
    db = allow(config, document_scope)
    _, c = document_evidence.load(config, db, document_scope[0], completed)
    before = db.execute("SELECT count(*) FROM model_calls").fetchone()[0]
    db.close()
    response = http.post(f"/admin/documents/{completed}/reviews", json=assessment(c, verdict))
    assert (
        response.status_code == 200 and response.json()["review_gate"]["publication_ready"] is ready
    )
    assert response.json()["review_gate"]["knowledge_verified"] is False
    result = http.post(f"/admin/documents/{completed}/publish")
    assert result.status_code == (200 if ready else 422)
    db = database.connect(config.state_root)
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == before
    db.close()


def test_admin_cookie_csrf_and_original_scope_enforced(config, document_scope, completed, http):
    db = allow(config, document_scope)
    other = auth.create_client(db, "untrusted", False)
    _, c = document_evidence.load(config, db, document_scope[0], completed)
    db.close()
    with TestClient(
        create_app(config), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 1)
    ) as client:
        r = client.post(
            f"/admin/documents/{completed}/reviews",
            json=assessment(c),
            headers={
                "Origin": "http://127.0.0.1:8765",
                "Authorization": "Bearer " + other["token"],
            },
        )
        assert r.status_code in (401, 403)
    assert (
        http.post(
            f"/admin/documents/{completed}/reviews",
            json=assessment(c),
            headers={"X-CSRF-Token": "wrong"},
        ).status_code
        == 403
    )
    assert (
        http.post(
            f"/admin/documents/{completed}/reviews",
            json=assessment(c),
            headers={"Origin": "http://evil.invalid"},
        ).status_code
        == 403
    )
    db = allow(config, document_scope, original=False)
    db.close()
    assert http.get(f"/admin/documents/{completed}/review-packet").status_code in (403, 404)
    assert http.get(f"/admin/documents/{completed}/source").status_code in (403, 404)
    assert http.post(f"/admin/documents/{completed}/reviews", json=assessment(c)).status_code in (
        403,
        404,
    )


def approved(config, scope, rid):
    db = allow(config, scope)
    _, c = document_evidence.load(config, db, scope[0], rid)
    document_review.record(db, rid, c, assessment(c))
    return db


def test_publish_preserves_source_and_manual_edits_search_provenance(
    config, document_scope, completed
):
    db = approved(config, document_scope, completed)
    _, c = document_evidence.load(config, db, document_scope[0], completed)
    assert not search.search(config, db, document_scope[0], "measured", [document_scope[1]])[
        "results"
    ]
    result = document_publication.publish_locked(config, db, completed)
    root = vaults.checked_vault(
        config, auth.grant(db, document_scope[0], document_scope[1], "query")
    )
    note = root / result["relative_path"] / "note.md"
    text = note.read_text()
    assert "knowledge_verified: false" in text
    hits = search.search(config, db, document_scope[0], "measured", [document_scope[1]])["results"]
    assert hits and hits[0]["source_kind"] == "published_document" and hits[0]["originals"]
    with pytest.raises(InvalidInput, match="frozen"):
        document_review.record(db, completed, c, assessment(c))
    note.write_text("Manual edited measured note")
    assert document_publication.publish_locked(config, db, completed)["historical"]
    assert note.read_text() == "Manual edited measured note"
    assert (
        search.search(config, db, document_scope[0], "measured", [document_scope[1]])["results"][0][
            "source_kind"
        ]
        == "user_revision"
    )
    note.unlink()
    document_publication.publish_locked(config, db, completed)
    assert not note.exists()
    task = document_bindings.get(db, completed)
    assert (
        document_bindings.authorize(
            config,
            db,
            db.execute("SELECT * FROM execution_runs WHERE id=?", (completed,)).fetchone(),
            task,
        ).data
        == document_scope[4]
    )
    db.close()


def test_publish_rename_crash_hidden_until_explicit_reconcile(config, document_scope, completed):
    db = approved(config, document_scope, completed)
    real = publication_files.rename_exclusive

    def after(*args):
        real(*args)
        raise OSError("synthetic after-rename crash")

    with patch.object(publication_files, "rename_exclusive", side_effect=after):
        with pytest.raises(OSError):
            document_publication.publish_locked(config, db, completed)
    assert db.execute("SELECT state FROM document_publications").fetchone()[0] == "publishing"
    assert not search.search(config, db, document_scope[0], "measured", [document_scope[1]])[
        "results"
    ]
    assert document_publication.publish_locked(config, db, completed)["state"] == "published"
    assert search.search(config, db, document_scope[0], "measured", [document_scope[1]])["results"]
    db.close()


def test_publish_preexisting_path_not_adopted_or_overwritten(config, document_scope, completed):
    db = approved(config, document_scope, completed)
    root = vaults.checked_vault(
        config, auth.grant(db, document_scope[0], document_scope[1], "query")
    )
    target = root / ("Document-" + completed)
    target.mkdir()
    (target / "note.md").write_text("human content")
    with pytest.raises(IntegrityError):
        document_publication.publish_locked(config, db, completed)
    with pytest.raises(IntegrityError, match="conflict"):
        document_publication.publish_locked(config, db, completed)
    assert (target / "note.md").read_text() == "human content"
    db.close()


@pytest.mark.parametrize("format", ["docx", "xlsx", "pptx", "chat"])
def test_generic_receipt_task_derived_queries_review_and_publication(config, store, policy, format):
    from fiber_bundle.chat_documents import extract as chat_extract

    source = chat(3) if format == "chat" else (FIXTURES / ("sample." + format)).read_bytes()
    mime = next(
        k
        for k, v in __import__("fiber_bundle.material_contract", fromlist=["MIMES"]).MIMES.items()
        if v == format
    )
    db = database.connect(config.state_root)
    client = auth.create_client(db, "synthetic generic", False)
    vid = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    auth.set_grant(db, client["id"], vid, query=True, original=True, add=True)
    receipt = intake(
        config,
        db,
        client["id"],
        Intake(
            vault_id=vid,
            event_id=format,
            source_key=format,
            session_id="synthetic",
            raw_event=base64.b64encode(b"synthetic").decode(),
            assets=[
                {
                    "role": "attachment",
                    "original_name": "fixture." + format,
                    "media_type": mime,
                    "data": base64.b64encode(source).decode(),
                }
            ],
        ),
    )
    asset = db.execute(
        "SELECT asset_id FROM version_assets WHERE version_id=?", (receipt["source_version_id"],)
    ).fetchone()[0]
    rid = execution_ledger.create_run(
        config,
        db,
        policy,
        client["id"],
        vid,
        receipt["source_version_id"],
        document=document_bindings.DocumentInput(
            receipt["job_id"], asset, DocumentPolicy("sha256:" + "b" * 64)
        ),
    )
    execution_ledger.start(config, db, rid, policy)
    db.close()

    def worker(config, policy, source, *, guard, task_id, format, profile, runtime):
        guard()
        root = config.state_root / "document-captures" / task_id
        for name in ("input", "output"):
            (root / name).mkdir(parents=True)
        document_capture.write_new(root / "input", "source.pdf", source)
        value = (
            chat_extract(source) if format == "chat" else office_documents.extract(source, format)
        )
        document_capture.write_new(root / "output", "result.json", canonical(value).encode())
        guard()
        return DocumentCapture(task_id, root, document_capture.verify(root / "output", source))

    with patch.object(document_worker, "execute_document", side_effect=worker):
        call(config, policy, rid)
    db = database.connect(config.state_root)
    db.execute("UPDATE execution_runs SET state='succeeded' WHERE id=?", (rid,))
    metadata, c = document_evidence.load(config, db, client["id"], rid)
    assert metadata["format"] == format and metadata["unit_count"] == len(c["segments"])
    assert document_evidence.search(config, db, client["id"], rid, "watts")["hits"]
    document_review.record(db, rid, c, assessment(c))
    if format == "chat":
        with pytest.raises(InvalidInput, match="complete"):
            document_publication.publish_locked(config, db, rid)
    else:
        assert document_publication.publish_locked(config, db, rid)["state"] == "published"
    db.close()


def test_protected_image_route_and_client_query_segment_contract(config, store, policy, http):
    # A real OOXML parser produces this capture; the process boundary is covered separately.
    from fiber_bundle import material_contract

    source = (FIXTURES / "sample.docx").read_bytes()
    db = database.connect(config.state_root)
    client = auth.create_client(db, "image route source owner", False)
    vid = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    auth.set_grant(db, client["id"], vid, query=True, original=True, add=True)
    receipt = intake(
        config,
        db,
        client["id"],
        Intake(
            vault_id=vid,
            event_id="image-route",
            source_key="image-route",
            session_id="synthetic",
            raw_event=base64.b64encode(b"synthetic").decode(),
            assets=[
                {
                    "role": "attachment",
                    "original_name": "test.docx",
                    "media_type": next(
                        k for k, v in material_contract.MIMES.items() if v == "docx"
                    ),
                    "data": base64.b64encode(source).decode(),
                }
            ],
        ),
    )
    asset = db.execute(
        "SELECT asset_id FROM version_assets WHERE version_id=?", (receipt["source_version_id"],)
    ).fetchone()[0]
    rid = execution_ledger.create_run(
        config,
        db,
        policy,
        client["id"],
        vid,
        receipt["source_version_id"],
        document=document_bindings.DocumentInput(
            receipt["job_id"], asset, DocumentPolicy("sha256:" + "b" * 64)
        ),
    )
    execution_ledger.start(config, db, rid, policy)
    task = document_bindings.get(db, rid)
    root = config.state_root / "document-captures" / task["capture_task_id"]
    for name in ("input", "output"):
        (root / name).mkdir(parents=True)
    document_capture.write_new(root / "input", "source.pdf", source)
    value = office_documents.extract(source, "docx")
    raw = canonical(value).encode()
    document_capture.write_new(root / "output", "result.json", raw)
    db.execute(
        "UPDATE document_tasks SET state='extracted',result_sha256=?,page_count=?,"
        "review_page_count=?,ocr_page_count=0 WHERE run_id=?",
        (
            material_contract.sha(raw),
            len(value["segments"]),
            sum(bool(u["review_reasons"]) for u in value["segments"]),
            rid,
        ),
    )
    db.execute("UPDATE execution_runs SET state='succeeded' WHERE id=?", (rid,))
    db.close()
    packet = http.get(f"/admin/documents/{rid}/review-packet")
    assert packet.status_code == 200
    item = value["attachments"][0]
    response = http.get(f"/admin/documents/{rid}/images/{item['sha256']}")
    assert response.status_code == 200 and response.content == base64.b64decode(item["data"])
    assert "img-src 'self'" in response.headers["content-security-policy"]
    headers = {"Authorization": "Bearer " + client["token"]}
    assert http.get(f"/v1/documents/{rid}/segments/u1", headers=headers).status_code == 200
    assert http.get(f"/v1/documents/{rid}/segments/nonexistent", headers=headers).status_code == 404
    db = database.connect(config.state_root)
    auth.set_grant(db, client["id"], vid, query=True, original=False, add=False)
    db.close()
    assert http.get(f"/admin/documents/{rid}/images/{item['sha256']}").status_code == 404
    assert http.get(f"/v1/documents/{rid}/segments/u1", headers=headers).status_code == 200


def test_publication_recovery_rejects_same_bytes_in_replaced_vault_root(
    config, document_scope, completed
):
    import shutil

    db = approved(config, document_scope, completed)
    real = publication_files.rename_exclusive

    def interrupted(*args):
        real(*args)
        raise OSError("synthetic crash")

    with patch.object(publication_files, "rename_exclusive", side_effect=interrupted):
        with pytest.raises(OSError):
            document_publication.publish_locked(config, db, completed)
    root = vaults.checked_vault(
        config, auth.grant(db, document_scope[0], document_scope[1], "query")
    )
    original = root.with_name("preserved-old-root")
    root.rename(original)
    shutil.copytree(original, root)
    with pytest.raises(IntegrityError, match="binding changed"):
        document_publication.publish_locked(config, db, completed)
    assert db.execute("SELECT state FROM document_publications").fetchone()[0] == "publishing"
    assert not search.search(config, db, document_scope[0], "measured", [document_scope[1]])[
        "results"
    ]
    db.close()


def test_partial_stage_never_recreated_or_published(config, document_scope, completed):
    db = approved(config, document_scope, completed)
    real = publication_files.stage

    def partial(root, relative, intended):
        real(root, relative, intended)
        (root / relative / "provenance.json").unlink()
        raise OSError("synthetic partial-stage crash")

    with patch.object(publication_files, "stage", side_effect=partial):
        with pytest.raises(OSError):
            document_publication.publish_locked(config, db, completed)
    with pytest.raises(IntegrityError):
        document_publication.publish_locked(config, db, completed)
    assert db.execute("SELECT state FROM document_publications").fetchone()[0] == "publishing"
    assert not search.search(config, db, document_scope[0], "measured", [document_scope[1]])[
        "results"
    ]
    db.close()
