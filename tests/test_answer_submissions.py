import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from test_api import add_client, permissions
from test_execution import policy as policy
from test_session_search import intake
from test_session_search import owner as owner

from fiber_bundle import answer_tasks, database, execution, execution_ledger, model_gateway
from fiber_bundle.api import create_app


@pytest.fixture
def enabled(config, policy, http):
    with TestClient(
        create_app(config, execution_policy=policy),
        base_url="http://127.0.0.1:8765",
        client=("127.0.0.1", 49000),
    ) as client:
        yield client


def payload(receipt, **extra):
    return {
        "request_id": "question-1",
        "receipt_id": receipt["job_id"],
        "session_id": "current",
        "question": "What pressure?",
        "retrieval_query": "psi",
        **extra,
    }


def submit(enabled, owner, data):
    return enabled.post("/v1/answer-tasks", headers=owner[1], json=data)


def test_disabled_by_default_and_no_unauthenticated_config_disclosure(http, owner, config):
    receipt = intake(http, owner)
    assert http.post("/v1/answer-tasks", json=payload(receipt)).status_code == 401
    result = http.post("/v1/answer-tasks", headers=owner[1], json=payload(receipt))
    assert result.status_code == 503 and result.json()["error"] == "answer_execution_not_configured"
    db = database.connect(config.state_root)
    assert db.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0] == 0
    db.close()


def test_submit_only_queues_and_same_request_reuses_task(enabled, http, owner, config):
    receipt = intake(http, owner)
    with patch.object(
        model_gateway, "complete", side_effect=AssertionError("must not invoke provider")
    ):
        first = submit(enabled, owner, payload(receipt))
        second = submit(enabled, owner, payload(receipt))
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json() and first.json()["state"] == "queued"
    assert first.json()["result_url"] is None and first.headers["cache-control"] == "no-store"
    db = database.connect(config.state_root)
    assert db.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 0
    assert (
        json.loads(answer_tasks.get(db, first.json()["run_id"])["request_json"])["submission_id"]
        == "question-1"
    )
    db.close()


@pytest.mark.parametrize(
    "change", [{"question": "Changed?"}, {"retrieval_query": "100"}, {"limit": 1}]
)
def test_reusing_request_id_for_changed_content_conflicts(enabled, http, owner, config, change):
    receipt = intake(http, owner)
    assert submit(enabled, owner, payload(receipt)).status_code == 202
    response = submit(enabled, owner, payload(receipt, **change))
    assert response.status_code == 409 and response.json()["error"] == "answer_request_conflict"
    db = database.connect(config.state_root)
    assert db.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0] == 1
    db.close()


@pytest.mark.parametrize(
    "change",
    [
        {"model": "other"},
        {"vault_ids": []},
        {"endpoint": "https://example.com"},
        {"request_id": ""},
        {"request_id": "\x00"},
        {"question": ""},
        {"limit": 21},
    ],
)
def test_forbidden_overrides_and_invalid_input(enabled, http, owner, config, change):
    receipt = intake(http, owner)
    assert submit(enabled, owner, payload(receipt, **change)).status_code == 422
    db = database.connect(config.state_root)
    assert db.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0] == 0
    db.close()


def test_status_is_owner_scoped_and_checks_revocation(enabled, http, owner):
    receipt = intake(http, owner)
    rid = submit(enabled, owner, payload(receipt)).json()["run_id"]
    other, headers = add_client(http, "other")
    permissions(http, other, owner[2]["personal"], query=True, add=True)
    assert enabled.get("/v1/answer-tasks/" + rid, headers=headers).status_code == 404
    assert (
        enabled.post("/v1/answer-tasks", headers=headers, json=payload(receipt)).status_code == 404
    )
    permissions(http, owner[0], owner[2]["personal"], query=False, original=True, add=True)
    assert enabled.get("/v1/answer-tasks/" + rid, headers=owner[1]).status_code == 404
    assert submit(enabled, owner, payload(receipt)).status_code == 404


def test_request_id_namespace_is_per_client(enabled, http, owner):
    one = intake(http, owner)
    rid = submit(enabled, owner, payload(one)).json()["run_id"]
    other, headers = add_client(http, "other")
    permissions(http, other, owner[2]["personal"], query=True, add=True)
    other_owner = (other, headers, owner[2])
    two = intake(http, other_owner, event="other")
    response = submit(enabled, other_owner, payload(two))
    assert response.status_code == 202 and response.json()["run_id"] != rid


def test_concurrent_identical_requests_create_exactly_one_task(enabled, http, owner, config):
    receipt = intake(http, owner)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: submit(enabled, owner, payload(receipt)), range(4)))
    assert all(r.status_code == 202 for r in responses)
    assert len({r.json()["run_id"] for r in responses}) == 1
    db = database.connect(config.state_root)
    assert db.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0] == 1
    db.close()


def test_restart_reuses_task_and_policy_change_conflicts(enabled, http, owner, config, policy):
    receipt = intake(http, owner)
    rid = submit(enabled, owner, payload(receipt)).json()["run_id"]
    for current_policy, expected in [(policy, 202), (replace(policy, max_output_tokens=256), 409)]:
        with TestClient(
            create_app(config, execution_policy=current_policy),
            base_url="http://127.0.0.1:8765",
            client=("127.0.0.1", 49000),
        ) as restarted:
            result = submit(restarted, owner, payload(receipt))
            assert result.status_code == expected
            if expected == 202:
                assert result.json()["run_id"] == rid


def test_status_tracks_execution_and_retrieval_without_leaking_request(
    enabled, http, owner, config, policy
):
    receipt = intake(http, owner)
    rid = submit(enabled, owner, payload(receipt)).json()["run_id"]
    db = database.connect(config.state_root)
    execution_ledger.start(config, db, rid, policy)
    assert enabled.get("/v1/answer-tasks/" + rid, headers=owner[1]).json()["state"] == "running"

    def provider(p, r, request):
        assert "question-1" not in json.dumps(request)
        data = json.loads(request["messages"][1]["content"])
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {"mode": "extractive", "quotations": data["quotations"]}
                        ),
                    },
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 20},
        }

    with patch.object(model_gateway, "complete", side_effect=provider):
        execution.handle(config, policy, rid, {"type": "model", "id": "1", "body": {}})
    execution_ledger.transition(db, rid, "succeeded")
    result = enabled.get("/v1/answer-tasks/" + rid, headers=owner[1])
    assert result.json()["result_url"] == "/v1/answers/" + rid
    assert "What pressure" not in result.text and policy.secret_file not in result.text
    answer = enabled.get(result.json()["result_url"], headers=owner[1])
    assert answer.status_code == 200 and answer.json()["model_used"] is True
    assert submit(enabled, owner, payload(receipt)).json()["state"] == "succeeded"
    assert db.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 1
    db.close()


def test_submission_only_requires_query_for_existing_receipt(enabled, http, owner):
    receipt = intake(http, owner)
    permissions(http, owner[0], owner[2]["personal"], query=True, original=False, add=False)
    assert submit(enabled, owner, payload(receipt)).status_code == 202


def test_server_cli_loads_policy_without_launching_execution(http, owner, config, policy, tmp_path):
    from dataclasses import asdict

    from fiber_bundle import cli

    receipt = intake(http, owner)
    policy_file = tmp_path / "execution.json"
    policy_file.write_text(json.dumps(asdict(policy)))
    argv = [
        "fiber-bundle",
        "serve",
        "--project-root",
        str(tmp_path),
        "--config",
        "config.json",
        "--execution-config",
        str(policy_file),
    ]

    def serve(app, **settings):
        assert settings["host"] == "127.0.0.1"
        with TestClient(
            app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 49000)
        ) as client:
            assert submit(client, owner, payload(receipt)).status_code == 202

    with (
        patch("sys.argv", argv),
        patch("uvicorn.run", side_effect=serve),
        patch.object(
            model_gateway, "complete", side_effect=AssertionError("no provider at startup")
        ),
    ):
        assert cli.main() == 0
