"""Observable contracts and ownership across the query transport/storage boundary."""

from contextlib import closing

import pytest
from test_local_query_tasks import body
from test_local_query_tasks import local_task as local_task
from test_local_query_tasks import route as route
from test_query_routing_preparation import routing as routing
from test_session_search import intake
from test_session_search import owner as owner

from fiber_bundle import (
    cloud_answer_tasks,
    database,
    local_query_ledger,
    query_jobs,
)


@pytest.mark.parametrize("role", ["local", "cloud"])
@pytest.mark.parametrize("action", ["detail", "result", "cancel"])
def test_admin_missing_task_remains_404(http, role, action):
    path = f"/admin/query/tasks/{role}/missing"
    response = (
        http.post(path + "/cancel")
        if action == "cancel"
        else http.get(path + ("/result" if action == "result" else ""))
    )
    assert response.status_code == 404
    assert response.json() == {"error": "request_denied"}


@pytest.mark.parametrize("action", ["recover", "cancel"])
def test_admin_missing_job_remains_404(http, action):
    assert http.post(f"/admin/query/jobs/missing/{action}").status_code == 404


@pytest.mark.parametrize("action", ["detail", "result", "cancel"])
def test_admin_task_checks_revoked_owner(http, owner, local_task, action):
    assert http.post(f"/admin/clients/{owner[0]}/revoke").status_code == 200
    path = f"/admin/query/tasks/local/{local_task[0]}"
    response = (
        http.post(path + "/cancel")
        if action == "cancel"
        else http.get(path + ("/result" if action == "result" else ""))
    )
    assert response.status_code == 401


def test_admin_lists_both_roles_and_cancels_linked_cloud_job(
    http, config, route, owner, local_task
):
    with closing(database.connect(config.state_root)) as db:
        job = query_jobs.submit(config, db, route, owner[0], "local", local_task[0], True)
        claim = local_query_ledger.claim(config, db, route, owner[0], local_task[0])
        local_query_ledger.finish(config, db, route, claim, body(route, claim))
        cloud = cloud_answer_tasks.submit(
            config, db, route, owner[0], local_task[0], "query-job:" + job["id"]
        )["run_id"]
        db.execute("UPDATE query_jobs SET cloud_run_id=? WHERE id=?", (cloud, job["id"]))
        # Deterministic ordering verifies the existing UNION projection contract.
        db.execute("UPDATE local_query_tasks SET created_at=1 WHERE id=?", (local_task[0],))
        db.execute("UPDATE execution_runs SET created_at=2 WHERE id=?", (cloud,))
    report = http.get("/admin/query/tasks").json()
    assert [(t["role"], t["task_id"]) for t in report["tasks"]] == [
        ("cloud", cloud),
        ("local", local_task[0]),
    ]
    assert all(t["client_id"] == owner[0] for t in report["tasks"])
    assert all(t["configuration_sha256"] == route.digest for t in report["tasks"])
    assert report["jobs"][0]["id"] == job["id"]
    other = http.post("/admin/clients", json={"name": "other", "create_vault": False}).json()
    assert (
        http.post(
            f"/v1/query-tasks/cloud/{cloud}/cancel",
            headers={"Authorization": "Bearer " + other["token"]},
        ).status_code
        == 404
    )
    response = http.post(f"/admin/query/tasks/cloud/{cloud}/cancel")
    assert response.status_code == 200
    assert response.json()["id"] == job["id"] and response.json()["state"] == "cancelled"
    assert http.get(f"/v1/query-tasks/cloud/{cloud}", headers=owner[1]).json()["state"] == (
        "cancelled"
    )
    with closing(database.connect(config.state_root)) as db:
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0


@pytest.mark.parametrize("kind", ["receipt", "notes", "scoped"])
@pytest.mark.parametrize("invalid", [{"limit": True}, {"endpoint": "http://invalid"}])
def test_local_request_contracts_remain_strict(http, owner, config, routing, kind, invalid):
    digest = http.put(
        "/admin/query/policy", json={"expected_sha256": None, "value": routing}
    ).json()["configuration_sha256"]
    common = {
        "request_id": "contract",
        "question": "What pressure?",
        "retrieval_query": "psi",
        "configuration_sha256": digest,
    }
    if kind == "receipt":
        receipt = intake(http, owner)
        path = "/v1/local-query-tasks"
        common.update(receipt_id=receipt["job_id"], session_id="current")
    elif kind == "notes":
        path = "/v1/local-note-query-tasks"
        common.update(vault_id=owner[2]["personal"])
    else:
        path = "/v1/local-scoped-query-tasks"
        common.update(vault_ids=[owner[2]["personal"]])
    assert http.post(path, headers=owner[1], json={**common, **invalid}).status_code == 422
    assert http.post(path, headers=owner[1], json=common).status_code == 202
    with closing(database.connect(config.state_root)) as db:
        assert db.execute("SELECT count(*) FROM local_query_tasks").fetchone()[0] == 1
        for table in ("local_query_calls", "model_calls", "query_jobs"):
            assert db.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0
