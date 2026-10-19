from unittest.mock import patch

from test_article_tasks import article_scope as article_scope
from test_document_creation_config import creation_policy as creation_policy
from test_document_creation_execution import prepared_task as prepared_task
from test_document_creation_lifecycle import attached as attached
from test_document_creation_lifecycle import completed as completed
from test_document_creation_tasks import input_task as input_task
from test_document_tasks import document_scope as document_scope
from test_execution import policy as policy
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner

from fiber_bundle import auth, database, operations
from fiber_bundle import document_creation_tasks as tasks


def test_cookie_admin_inventory_no_sidecar_creation(http, config):
    assert http.get("/admin/backend").json()["reminder_history"] == []
    assert not (config.state_root / "weixin-inbox.sqlite3").exists()
    assert http.get("/admin/document-tasks").json() == {"tasks": []}
    assert not (config.state_root / "document-creation.sqlite3").exists()
    assert http.get("/admin/inventory/receipts").json() == []
    assert http.get("/admin/inventory/executions").json() == {
        "runs": [],
        "daily_costs": [],
        "limit": 100,
        "has_more": False,
    }
    assert http.get("/admin/inventory/components").json()["update_automatic"] is False
    http.cookies.clear()
    for route in [
        "/admin/inventory/receipts",
        "/admin/inventory/executions",
        "/admin/inventory/components",
        "/admin/document-tasks",
    ]:
        assert http.get(route).status_code == 401


def test_component_check_is_explicit_authorized_and_detects_drift(http, tmp_path, monkeypatch):
    import shutil
    from pathlib import Path

    from fiber_bundle import management_inventory

    path = "/admin/inventory/components/check"
    assert http.post(path, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    current = http.post(path).json()
    assert current["state"] == "matched"
    assert current["upstream"] == current["asr_weights"] == "not_checked"
    assert current["running_tasks_changed"] is False
    source = Path(management_inventory.__file__).parent / "vendor"
    shutil.copytree(source, tmp_path / "vendor", ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(management_inventory, "__file__", str(tmp_path / "inventory.py"))
    (tmp_path / "vendor" / "article_parser.py").write_text("synthetic changed implementation")
    assert http.post(path).json()["state"] == "changed"


def test_reconcile_cleans_article_worker_under_existing_http_lock(
    http, config, policy, article_scope
):
    from test_article_tasks import create

    from fiber_bundle import execution_ledger

    rid = create(config, policy, article_scope)
    db = database.connect(config.state_root)
    execution_ledger.start(config, db, rid, policy)
    db.execute("UPDATE article_tasks SET state='capturing' WHERE run_id=?", (rid,))
    cid = db.execute("SELECT capture_task_id FROM article_tasks WHERE run_id=?", (rid,)).fetchone()[
        0
    ]
    with (
        patch("fiber_bundle.management_routes.execution.stop"),
        patch("fiber_bundle.article_worker.remove_container") as remove,
    ):
        response = http.post("/admin/inventory/executions/" + rid + "/reconcile")
    assert response.status_code == 200 and response.json()["state"] == "interrupted"
    remove.assert_called_once_with("fiber-capture-" + cid)
    assert (
        db.execute("SELECT state FROM article_tasks WHERE run_id=?", (rid,)).fetchone()[0]
        == "interrupted"
    )
    db.close()


def test_reconcile_cleans_document_worker_under_existing_http_lock(
    http, config, policy, document_scope
):
    from test_document_tasks import start

    rid = start(config, policy, document_scope)
    db = database.connect(config.state_root)
    db.execute("UPDATE document_tasks SET state='extracting' WHERE run_id=?", (rid,))
    cid = db.execute(
        "SELECT capture_task_id FROM document_tasks WHERE run_id=?", (rid,)
    ).fetchone()[0]
    with (
        patch("fiber_bundle.management_routes.execution.stop"),
        patch("fiber_bundle.document_worker.stop_owned") as remove,
    ):
        response = http.post("/admin/inventory/executions/" + rid + "/reconcile")
    assert response.status_code == 200 and response.json()["state"] == "interrupted"
    assert remove.call_args.args[2] == cid
    assert (
        db.execute("SELECT state FROM document_tasks WHERE run_id=?", (rid,)).fetchone()[0]
        == "interrupted"
    )
    db.close()


def test_document_intent_submit_revision_cancel_no_model(http, config, owner, input_task):
    cid = owner[0]
    body = {
        "client_id": cid,
        "submission_id": "m12-doc",
        "query_task_id": input_task[0],
        "requirements": "Prepare a summary",
        "requested_format": "md",
    }
    assert (
        http.post("/admin/document-tasks", json=body, headers={"X-CSRF-Token": "wrong"}).status_code
        == 403
    )
    prepared = http.post("/admin/document-tasks", json=body).json()
    assert http.post("/admin/document-tasks", json=body).json() == prepared
    tid = prepared["task_id"]
    assert http.get("/admin/document-tasks/" + tid).json()["revision"] == 1
    amendment = {
        "expected_revision": prepared["revision"],
        "expected_sha256": prepared["plan_sha256"],
        "amendment_id": "second",
        "addition": "Add a table",
    }
    next_version = http.post("/admin/document-tasks/" + tid + "/append", json=amendment)
    assert next_version.status_code == 200
    assert next_version.json()["revision"] == 2
    assert (
        http.post(
            "/admin/document-tasks/" + tid + "/cancel",
            json={"expected_revision": 1, "expected_sha256": prepared["plan_sha256"]},
        ).status_code
        == 422
    )
    latest = next_version.json()
    assert (
        http.post(
            "/admin/document-tasks/" + tid + "/cancel",
            json={
                "expected_revision": latest["revision"],
                "expected_sha256": latest["plan_sha256"],
            },
        ).json()["state"]
        == "cancelled"
    )
    db = database.connect(config.state_root)
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    db.close()


def test_document_draft_authority_and_scope_save(http, config, completed):
    db, policy, attempt, raw, metadata = completed
    tid = attempt.task_id
    status = http.get("/admin/document-tasks/" + tid)
    assert status.status_code == 200
    assert status.json()["execution"]["state"] == "succeeded"
    draft = http.get("/admin/document-tasks/" + tid + "/draft")
    assert draft.status_code == 200 and draft.content == raw
    assert draft.headers["content-disposition"].startswith("attachment;")
    auth.revoke_client(db, attempt.client_id)
    assert http.get("/admin/document-tasks/" + tid + "/draft").status_code == 401


def test_bearer_cannot_authorize_admin_route(http, config, owner, prepared_task):
    http.cookies.clear()
    http.headers["Authorization"] = owner[1]["Authorization"]
    assert http.get("/admin/document-tasks/" + prepared_task["task_id"]).status_code == 401


def test_original_download_requires_selected_current_client_scope(
    http, config, owner, request_data
):
    cid = owner[0]
    # A session-scoped source keeps the tested original authorization boundary direct.
    db = database.connect(config.state_root)
    from fiber_bundle.store import Store

    Store(config).ingest_locked(db, **{**request_data, "owner_client_id": cid})
    vault = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    db.execute(
        "UPDATE client_vault_grants SET can_original=1 WHERE client_id=? AND vault_id=?",
        (cid, vault),
    )
    rows = operations.versions(config, db, cid, vault, 0, 20)
    version = rows[0]
    asset = version["assets"][0]
    path = f"/admin/inventory/original/{vault}/{version['id']}/{asset['id']}?client_id={cid}"
    assert http.get(path).status_code == 200
    auth.revoke_client(db, cid)
    assert http.get(path).status_code == 404
    db.close()


def test_running_owner_cannot_be_reconciled_or_replayed(http, config, attached):
    from fiber_bundle.executor_container import run_lock

    db, policy, guard, attempt = attached
    rid = attempt.run_id
    with patch("fiber_bundle.management_routes.execution.stop") as stop:
        assert http.post("/admin/inventory/executions/" + rid + "/reconcile").status_code == 422
        stop.assert_not_called()
    with run_lock(config, rid), patch("fiber_bundle.management_routes.execution.stop") as stop:
        assert http.post("/admin/inventory/executions/" + rid + "/reconcile").status_code == 422
        stop.assert_not_called()
    assert http.post("/admin/inventory/executions/" + rid + "/cancel").status_code == 200
    assert (
        db.execute("SELECT state FROM model_calls WHERE run_id=?", (rid,)).fetchone()[0]
        == "uncertain"
    )
    assert (
        tasks.status(config, db, attempt.client_id, attempt.task_id)["execution"]["state"]
        == "cancelled"
    )
