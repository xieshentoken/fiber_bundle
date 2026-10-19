import pytest
from test_local_query_tasks import local_task as local_task
from test_local_query_tasks import route as route
from test_query_routing_preparation import routing as routing
from test_session_search import intake
from test_session_search import owner as owner

from fiber_bundle import database, query_policy_store


def test_policy_requires_admin_and_csrf_and_cas(http, owner, routing):
    assert (
        http.put(
            "/admin/query/policy", json={"expected_sha256": None, "value": routing}
        ).status_code
        == 200
    )
    assert (
        http.put(
            "/admin/query/policy", json={"expected_sha256": None, "value": routing}
        ).status_code
        == 422
    )
    cookies = dict(http.cookies)
    http.cookies.clear()
    assert http.get("/admin/query/policy", headers=owner[1]).status_code == 401
    assert (
        http.post(
            "/admin/query/jobs",
            json={"client_id": owner[0], "role": "local", "task_id": "missing"},
            headers=owner[1],
        ).status_code
        == 401
    )
    http.cookies.update(cookies)
    assert (
        http.put(
            "/admin/query/policy",
            json={"expected_sha256": None, "value": routing},
            headers={"X-CSRF-Token": ""},
        ).status_code
        == 403
    )


def test_owned_queue_status_cancel_no_inference_or_cross_client(http, owner, config, routing):
    digest = http.put(
        "/admin/query/policy", json={"expected_sha256": None, "value": routing}
    ).json()["configuration_sha256"]
    receipt = intake(http, owner)
    payload = {
        "request_id": "api-local",
        "receipt_id": receipt["job_id"],
        "session_id": "current",
        "question": "What pressure?",
        "retrieval_query": "psi",
        "configuration_sha256": digest,
    }
    response = http.post("/v1/local-query-tasks", json=payload, headers=owner[1])
    assert response.status_code == 202
    tid = response.json()["task_id"]
    assert (
        http.post("/v1/local-query-tasks", json=payload, headers=owner[1]).json()["task_id"] == tid
    )
    assert (
        http.post(
            "/v1/local-query-tasks", json={**payload, "client_id": owner[0]}, headers=owner[1]
        ).status_code
        == 422
    )
    assert (
        http.post(
            "/v1/local-query-tasks",
            json={**payload, "configuration_sha256": "a" * 64},
            headers=owner[1],
        ).status_code
        == 422
    )
    other = http.post("/admin/clients", json={"name": "other", "create_vault": False}).json()
    assert (
        http.get(
            f"/v1/query-tasks/local/{tid}", headers={"Authorization": "Bearer " + other["token"]}
        ).status_code
        == 404
    )
    assert http.get(f"/v1/query-tasks/local/{tid}/result", headers=owner[1]).status_code == 422
    assert (
        http.post(f"/v1/query-tasks/local/{tid}/cancel", headers=owner[1]).json()["state"]
        == "cancelled"
    )
    db = database.connect(config.state_root)
    for table in ("local_query_calls", "model_calls", "query_jobs"):
        assert db.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0
    db.close()


def test_admin_list_queue_and_strict_job_contract(http, owner, config, routing, monkeypatch):
    digest = http.put(
        "/admin/query/policy", json={"expected_sha256": None, "value": routing}
    ).json()["configuration_sha256"]
    db = database.connect(config.state_root)
    vid = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    db.close()
    response = http.post(
        "/admin/query/tasks",
        json={
            "client_id": owner[0],
            "configuration_sha256": digest,
            "request_id": "notes",
            "vault_ids": [vid],
            "question": "What pressure?",
            "retrieval_query": "psi",
        },
    )
    assert response.status_code == 202
    tid = response.json()["task_id"]
    from fiber_bundle import query_job_launch

    monkeypatch.setattr(query_job_launch, "launch", lambda *args: None)
    payload = {"client_id": owner[0], "role": "local", "task_id": tid, "cloud_edit": False}
    job = http.post("/admin/query/jobs", json=payload)
    assert job.status_code == 202
    assert (
        http.post("/admin/query/jobs", json={**payload, "endpoint": "http://evil"}).status_code
        == 422
    )
    assert http.get("/admin/query/tasks").json()["jobs"][0]["id"] == job.json()["id"]
    assert http.get("/v1/query-jobs/" + job.json()["id"], headers=owner[1]).status_code == 200
    assert (
        http.post("/admin/query/jobs/" + job.json()["id"] + "/cancel").json()["state"]
        == "cancelled"
    )
    assert (
        http.post(
            "/admin/query/tasks/cloud/no-run/backup",
            json={
                "client_id": owner[0],
                "backup_index": 1,
                "selection_id": "one",
                "approve": False,
            },
        ).status_code
        == 422
    )


def test_admin_backup_uses_task_version_and_clients_cannot_approve(
    http, owner, config, route, local_task
):
    import json
    from dataclasses import asdict, replace

    from test_local_query_tasks import body

    from fiber_bundle import cloud_answer_tasks, local_query_ledger

    route = replace(route, approved_backups=(replace(route.cloud_answer, model="approved-other"),))
    value = json.loads(
        json.dumps({"schema": "query-routing-preparation/v1", "enabled": False, **asdict(route)})
    )
    query_policy_store.save(config, value, None)
    # This task binds the route before any execution, preserving the approved model list.
    db = database.connect(config.state_root)
    db.execute(
        "UPDATE local_query_tasks SET config_digest=? WHERE id=?", (route.digest, local_task[0])
    )
    claim = local_query_ledger.claim(config, db, route, owner[0], local_task[0])
    local_query_ledger.finish(config, db, route, claim, body(route, claim))
    cloud = cloud_answer_tasks.submit(config, db, route, owner[0], local_task[0], "api-cloud")[
        "run_id"
    ]
    db.close()
    details = http.get("/admin/query/tasks/cloud/" + cloud).json()
    assert details["backup_choices"][0]["model"] == "approved-other"
    assert "secret_file" not in str(details)
    selection = {
        "client_id": owner[0],
        "selection_id": "api-choice",
        "backup_index": 1,
        "approve": True,
    }
    cookies = dict(http.cookies)
    http.cookies.clear()
    assert (
        http.post(
            "/admin/query/tasks/cloud/" + cloud + "/backup", json=selection, headers=owner[1]
        ).status_code
        == 401
    )
    http.cookies.update(cookies)
    assert (
        http.post("/admin/query/tasks/cloud/" + cloud + "/backup", json=selection).json()["state"]
        == "selected"
    )
    assert (
        http.get("/admin/query/tasks/cloud/" + cloud).json()["selections"][0]["approval_kind"]
        == "trusted_host_explicit"
    )
    db = database.connect(config.state_root)
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    db.close()


def test_backup_approval_rejects_non_boolean():
    from pydantic import ValidationError

    from fiber_bundle.query_http import BackupSelection

    for value in (1, False, "true", None):
        with pytest.raises(ValidationError):
            BackupSelection(client_id="owner", selection_id="one", backup_index=1, approve=value)
