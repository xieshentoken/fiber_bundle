import hashlib
import json
from unittest.mock import patch

import pytest
from document_support import candidate
from fastapi.testclient import TestClient
from test_document_tasks import (
    call,
    start,
    worker_fixture,
)
from test_document_tasks import (
    document_scope as document_scope,
)
from test_execution import policy as policy

from fiber_bundle import (
    auth,
    database,
    document_capture,
    document_review,
    document_worker,
    objects,
    search,
)
from fiber_bundle import (
    document_evidence as evidence,
)
from fiber_bundle.api import create_app
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.document_worker import DocumentCapture
from fiber_bundle.inputs import canonical
from fiber_bundle.operations import original

TEXT = (
    "Introductory native candidate evidence.\r\n"
    + "x " * 160
    + "Measured ＡＢＣ１２ value 12 watts."
)


def multipage_worker(config, policy, source, *, guard, task_id):
    guard()
    root = config.state_root / "document-captures" / task_id
    for name in ("input", "output"):
        (root / name).mkdir(parents=True)
    result = candidate(source, TEXT)
    blank = candidate(source, "")["pages"][0]
    blank.update(physical_page=2, pdf_page_label="ii")
    result.update(page_count=2, pages_needing_ocr=[2], pages_needing_review=[1, 2])
    result["pages"].append(blank)
    document_capture.write_new(root / "input", "source.pdf", source)
    document_capture.write_new(root / "output", "result.json", canonical(result).encode())
    guard()
    return DocumentCapture(task_id, root, document_capture.verify(root / "output", source))


@pytest.fixture
def completed(config, policy, document_scope):
    rid = start(config, policy, document_scope)
    with patch.object(document_worker, "execute_document", side_effect=multipage_worker):
        call(config, policy, rid)
    db = database.connect(config.state_root)
    db.execute("UPDATE execution_runs SET state='succeeded' WHERE id=?", (rid,))
    auth.set_grant(db, document_scope[0], document_scope[1], query=True, original=False, add=False)
    db.close()
    return rid


def test_query_only_located_evidence_blank_page_and_readonly(config, document_scope, completed):
    cid, vault, receipt, asset, _ = document_scope
    db = database.connect(config.state_root)
    before = config.state_root / "metadata.sqlite3"
    files = {
        p: p.read_bytes()
        for p in (config.state_root / "document-captures").rglob("*")
        if p.is_file()
    }
    digest = hashlib.sha256(before.read_bytes()).hexdigest()
    result = evidence.search(config, db, cid, completed, "abc12")
    assert len(result["hits"]) == 1
    hit = result["hits"][0]
    assert hit["physical_page"] == 1 and hit["pdf_page_label"] == "i"
    assert hit["printed_page_label_candidate"] is None
    assert hit["line"] == 2 and hit["start_char"] > TEXT.index("\r\n")
    assert TEXT[hit["start_char"] : hit["end_char"]] == hit["snippet"]
    assert hashlib.sha256(hit["snippet"].encode()).hexdigest() == hit["snippet_sha256"]
    assert hit["text_sha256"] == hashlib.sha256(TEXT.encode()).hexdigest()
    assert hit["coordinate_status"] == "unavailable"
    assert (
        result["source_version_id"] == receipt["source_version_id"] and result["asset_id"] == asset
    )
    assert result["published"] is False and result["knowledge_verified"] is False
    assert result["review_gate"]["state"] == "blocked" and result["model_used"] is False
    assert evidence.page(config, db, cid, completed, 2)["page"]["native_status"] == "missing"
    assert not evidence.search(config, db, cid, completed, "unknown")["hits"]
    assert not evidence.search(config, db, cid, completed, "abc123")["hits"]
    assert len(evidence.search(config, db, cid, completed, "measured watts")["hits"]) == 1
    with pytest.raises(auth.Denied):
        original(config, db, cid, vault, receipt["source_version_id"], asset, 8 * 1024 * 1024)
    # Candidate-only data does not become a note or enter ordinary search.
    assert not search.search(config, db, cid, "measured", [vault], 10)["results"]
    db.close()
    assert hashlib.sha256(before.read_bytes()).hexdigest() == digest
    assert all(p.read_bytes() == data for p, data in files.items())


@pytest.mark.parametrize(
    "query,download,add",
    [(q, o, a) for q in (False, True) for o in (False, True) for a in (False, True)],
)
def test_delivery_permission_matrix(config, document_scope, completed, query, download, add):
    db = database.connect(config.state_root)
    cid, vid, *_ = document_scope
    auth.set_grant(db, cid, vid, query=query, original=download, add=add)
    with patch.object(objects, "read_object", wraps=objects.read_object) as reads:
        if query:
            assert evidence.overview(config, db, cid, completed)["page_count"] == 2
        else:
            with pytest.raises(auth.Denied):
                evidence.page(config, db, cid, completed, 1)
            reads.assert_not_called()
    db.close()


@pytest.mark.parametrize(
    "kind",
    ["owner", "inactive", "vault", "version", "receipt_owner", "receipt_missing", "derived_weixin"],
)
def test_scope_rejected_before_candidate_read(config, document_scope, completed, kind):
    db = database.connect(config.state_root)
    cid, vid, receipt, *_ = document_scope
    if kind == "owner":
        other = auth.create_client(db, "other", False)
        auth.set_grant(db, other["id"], vid, query=True, original=True, add=True)
        cid = other["id"]
    elif kind == "inactive":
        db.execute("UPDATE clients SET active=0 WHERE id=?", (cid,))
    elif kind == "vault":
        other = db.execute("SELECT id FROM vaults WHERE name='supplier'").fetchone()[0]
        auth.set_grant(db, cid, other, query=True, original=True, add=True)
        db.execute("UPDATE execution_runs SET vault_id=? WHERE id=?", (other, completed))
    elif kind == "version":
        db.execute(
            "UPDATE execution_runs SET binding_kind='notes',version_id=NULL WHERE id=?",
            (completed,),
        )
    elif kind == "receipt_owner":
        other = auth.create_client(db, "other", False)
        db.execute(
            "UPDATE receipts SET owner_client_id=? WHERE job_id=?", (other["id"], receipt["job_id"])
        )
    elif kind == "receipt_missing":
        db.execute("UPDATE receipts SET state='incomplete' WHERE job_id=?", (receipt["job_id"],))
    else:
        db.execute(
            "UPDATE receipts SET event_id='weixin-task:unsupported' WHERE job_id=?",
            (receipt["job_id"],),
        )
    with patch.object(evidence.document_tasks, "verified_files") as reads:
        with pytest.raises(auth.Denied):
            evidence.search(config, db, cid, completed, "measured")
        reads.assert_not_called()
    db.close()


@pytest.mark.parametrize("state", ["queued", "running", "cancelled", "failed", "interrupted"])
def test_incomplete_or_stopped_run_never_delivers(config, document_scope, completed, state):
    db = database.connect(config.state_root)
    db.execute("UPDATE execution_runs SET state=? WHERE id=?", (state, completed))
    with patch.object(evidence.document_tasks, "verified_files") as reads:
        with pytest.raises(InvalidInput):
            evidence.page(config, db, document_scope[0], completed, 1)
        reads.assert_not_called()
    db.close()


@pytest.mark.parametrize("kind", ["source", "input", "candidate", "result_sha", "count", "asset"])
def test_tampering_rejects_evidence(config, document_scope, completed, kind):
    db = database.connect(config.state_root)
    task = db.execute("SELECT * FROM document_tasks WHERE run_id=?", (completed,)).fetchone()
    root = config.state_root / "document-captures" / task["capture_task_id"]
    if kind == "source":
        db.execute(
            "UPDATE document_tasks SET source_sha256=? WHERE run_id=?", ("a" * 64, completed)
        )
    elif kind == "input":
        (root / "input/source.pdf").write_bytes(b"%PDF-1.4 changed")
    elif kind == "candidate":
        path = root / "output/result.json"
        result = json.loads(path.read_text())
        result["publication_ready"] = True
        path.write_text(canonical(result))
    elif kind == "result_sha":
        db.execute(
            "UPDATE document_tasks SET result_sha256=? WHERE run_id=?", ("a" * 64, completed)
        )
    elif kind == "count":
        db.execute("UPDATE document_tasks SET review_page_count=0 WHERE run_id=?", (completed,))
    else:
        db.execute(
            "UPDATE version_assets SET media_type='text/plain' WHERE asset_id=?",
            (document_scope[3],),
        )
    with pytest.raises((auth.Denied, IntegrityError, InvalidInput)):
        evidence.page(config, db, document_scope[0], completed, 1)
    db.close()


@pytest.mark.parametrize(
    "query,limit",
    [
        ("", 1),
        ("*", 1),
        ("x\ny", 1),
        ("x " * 201, 1),
        ("a b c d e f g h i", 1),
        ("measured", 0),
        ("measured", 21),
        ("measured", True),
    ],
)
def test_invalid_search_is_bounded(config, document_scope, completed, query, limit):
    db = database.connect(config.state_root)
    with pytest.raises(InvalidInput):
        evidence.search(config, db, document_scope[0], completed, query, limit)
    db.close()


def test_gate_no_heuristic_does_not_mean_approval(config, policy, document_scope):
    rid = start(config, policy, document_scope)
    with patch.object(document_worker, "execute_document", side_effect=worker_fixture):
        call(config, policy, rid)
    db = database.connect(config.state_root)
    db.execute("UPDATE execution_runs SET state='succeeded' WHERE id=?", (rid,))
    auth.set_grant(db, document_scope[0], document_scope[1], query=True, original=False, add=False)
    _, result = evidence.load(config, db, document_scope[0], rid)
    gate = document_review.gate(result)
    assert gate["pages"][0]["review_reasons"] == []
    assert gate["state"] == "blocked" and gate["review_state"] == "not_run"
    with pytest.raises(InvalidInput):
        document_review.require_publication(result)
    db.close()


@pytest.mark.parametrize("number", [0, 301, True, "i"])
def test_invalid_physical_page(config, document_scope, completed, number):
    db = database.connect(config.state_root)
    with pytest.raises(InvalidInput):
        evidence.page(config, db, document_scope[0], completed, number)
    db.close()


@pytest.mark.parametrize("kind", ["symlink", "duplicate_json", "candidate_state", "no_ocr_flags"])
def test_integrity_failures_are_content_free_over_http(config, document_scope, completed, kind):
    db = database.connect(config.state_root)
    token = "synthetic-document-token-for-http-errors"
    db.execute(
        "UPDATE clients SET token_digest=? WHERE id=?", (auth.digest(token), document_scope[0])
    )
    task = db.execute("SELECT * FROM document_tasks WHERE run_id=?", (completed,)).fetchone()
    db.close()
    path = config.state_root / "document-captures" / task["capture_task_id"] / "output/result.json"
    if kind == "symlink":
        other = config.project_root / "outside-candidate.json"
        other.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(other)
    elif kind == "duplicate_json":
        raw = path.read_text()
        path.write_text('{"schema":1,' + raw[1:])
    elif kind == "candidate_state":
        db = database.connect(config.state_root)
        db.execute("UPDATE document_tasks SET state='failed' WHERE run_id=?", (completed,))
        db.close()
    else:
        value = json.loads(path.read_text())
        value["pages_needing_ocr"] = []
        path.write_text(canonical(value))
    with TestClient(
        create_app(config), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 49901)
    ) as http:
        response = http.get(
            f"/v1/documents/{completed}/pages/1", headers={"Authorization": "Bearer " + token}
        )
        assert response.status_code in (422, 503)
        assert set(response.json()) == {"error"} and "Measured" not in response.text


def test_http_auth_scope_errors_and_revoke(config, document_scope, completed):
    db = database.connect(config.state_root)
    owner = auth.create_client(db, "fresh-owner-token", False)
    # The fixture exposes an identity, not its secret. Replace only its token digest for HTTP.
    db.execute("DELETE FROM clients WHERE id=?", (owner["id"],))
    db.execute(
        "UPDATE clients SET token_digest=? WHERE id=?",
        (auth.digest(owner["token"]), document_scope[0]),
    )
    db.close()
    url = f"/v1/documents/{completed}"
    with TestClient(
        create_app(config), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 49900)
    ) as http:
        assert http.get(url).status_code == 401
        http.headers["Authorization"] = "Bearer " + owner["token"]
        assert http.get(url).json()["review_gate"]["state"] == "blocked"
        page = http.get(url + "/pages/1")
        assert page.status_code == 200 and page.json()["page"]["text"] == TEXT
        assert page.headers["cache-control"] == "no-store"
        assert http.post(url + "/search", json={"query": "abc12"}).json()["hits"]
        assert (
            http.post(
                url + "/search", json={"query": "measured", "publication_ready": True}
            ).status_code
            == 422
        )
        assert http.get(url + "/pages/3").status_code == 404
        assert http.get(url + "/pages/0").status_code == 422
        assert http.get(url + "/pages/i").status_code == 422
        assert http.get("/v1/documents/unknown").status_code == 404
        db = database.connect(config.state_root)
        auth.set_grant(
            db, document_scope[0], document_scope[1], query=False, original=True, add=True
        )
        db.close()
        response = http.get(url + "/pages/1")
        assert response.status_code == 404 and response.json() == {"error": "request_denied"}
        assert "Measured" not in response.text
