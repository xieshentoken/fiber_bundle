"""Document administrator read behavior after separating storage projections."""

from contextlib import closing

import pytest
from test_document_evidence import completed as completed
from test_document_evidence import document_scope as document_scope
from test_document_tasks import create
from test_execution import policy as policy

from fiber_bundle import auth, database, document_bindings, execution_ledger, objects


@pytest.mark.parametrize("suffix", ["review-packet", "source", "images/missing"])
def test_missing_document_run_remains_404(http, suffix):
    response = http.get(f"/admin/documents/missing/{suffix}")
    assert response.status_code == 404
    assert response.json() == {"error": "request_denied"}


def test_document_listing_bounded_ordered_and_excludes_other_run_types(
    http, config, policy, document_scope
):
    ids = []
    with closing(database.connect(config.state_root)) as db:
        for n in range(51):
            rid = create(config, policy, document_scope)
            db.execute("UPDATE execution_runs SET created_at=? WHERE id=?", (n, rid))
            ids.append(rid)
        auth.set_grant(
            db, document_scope[0], document_scope[1], query=True, original=True, add=True
        )
        other = execution_ledger.create_run(
            config,
            db,
            policy,
            document_scope[0],
            document_scope[1],
            document_scope[2]["source_version_id"],
        )
    response = http.get("/admin/documents")
    assert response.status_code == 200
    tasks = response.json()["tasks"]
    assert [t["run_id"] for t in tasks] == list(reversed(ids[1:]))
    assert other not in {t["run_id"] for t in tasks}
    assert all(
        t
        == {
            "run_id": rid,
            "state": "queued",
            "run_state": "queued",
            "client_id": document_scope[0],
            "vault_id": document_scope[1],
            "page_count": None,
            "binding_kind": "source",
            "publication_state": None,
            "automatic_state": None,
        }
        for t, rid in zip(tasks, reversed(ids[1:]), strict=True)
    )


def test_source_exact_bytes_mime_filename_and_readonly_packet(
    http, config, document_scope, completed
):
    with closing(database.connect(config.state_root)) as db:
        auth.set_grant(
            db, document_scope[0], document_scope[1], query=True, original=True, add=False
        )
        before_calls = db.execute("SELECT count(*) FROM model_calls").fetchone()[0]
    response = http.get(f"/admin/documents/{completed}/source")
    assert response.status_code == 200
    assert response.content == document_scope[4]
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="source.pdf"'
    packet = http.get(f"/admin/documents/{completed}/review-packet")
    assert packet.status_code == 200
    value = packet.json()
    assert value["source_version_id"] == document_scope[2]["source_version_id"]
    assert value["asset_id"] == document_scope[3]
    assert [u["unit_id"] for u in value["units"]] == ["P1", "P2"]
    assert value["published"] is False and value["knowledge_verified"] is False
    assert value["review_gate"]["publication_ready"] is False
    with closing(database.connect(config.state_root)) as db:
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == before_calls
        for table in ("document_reviews", "document_publications"):
            assert db.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0


@pytest.mark.parametrize("suffix", ["", "/review-packet", "/source", "/images/missing"])
def test_bearer_cannot_read_administrator_documents(
    http, config, document_scope, completed, suffix
):
    with closing(database.connect(config.state_root)) as db:
        client = auth.create_client(db, "bearer-only", False)
    http.cookies.clear()
    path = "/admin/documents" + ("/" + completed + suffix if suffix else "")
    response = http.get(path, headers={"Authorization": "Bearer " + client["token"]})
    assert response.status_code == 401


@pytest.mark.parametrize("change", ["query", "original", "revoke", "source_missing"])
@pytest.mark.parametrize("suffix", ["review-packet", "source"])
def test_document_reads_recheck_permissions_and_source(
    http, config, document_scope, completed, change, suffix
):
    with closing(database.connect(config.state_root)) as db:
        auth.set_grant(
            db, document_scope[0], document_scope[1], query=True, original=True, add=False
        )
        if change in ("query", "original"):
            auth.set_grant(
                db,
                document_scope[0],
                document_scope[1],
                query=change != "query",
                original=change != "original",
                add=False,
            )
        elif change == "revoke":
            auth.revoke_client(db, document_scope[0])
        else:
            task = document_bindings.get(db, completed)
            relative = objects.object_relative(document_scope[1], task["source_sha256"])
            (config.state_root / relative).unlink()
    response = http.get(f"/admin/documents/{completed}/{suffix}")
    assert (
        response.status_code
        == {"query": 404, "original": 404, "revoke": 404, "source_missing": 503}[change]
    )
