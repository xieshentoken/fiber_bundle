"""Synthetic Feishu caller probes existing backend and dedicated receiver boundaries.

Labels and headers never confer capabilities. This is not a live Feishu tool-policy test.
"""

import copy
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from test_api import add_client, payload, permissions
from test_execution import policy as policy
from test_session_search import intake
from test_session_search import owner as owner
from test_weixin_gateway import Platform
from test_weixin_gateway import setup as setup
from test_weixin_inbox import binding as binding

from fiber_bundle import database, model_gateway
from fiber_bundle import weixin_gateway as gateway
from fiber_bundle.api import create_app
from fiber_bundle.config import InvalidInput
from fiber_bundle.weixin_receiver_config import ReceiverConfig

PRIVATE = "CHANNEL_PRIVATE_EVIDENCE"


def snapshot(config):
    db = database.connect(config.state_root)
    try:
        return {
            "grants": [
                tuple(r) for r in db.execute("SELECT * FROM client_vault_grants ORDER BY 1,2")
            ],
            "clients": [
                tuple(r)
                for r in db.execute("SELECT id,active,create_vault FROM clients ORDER BY id")
            ],
            "counts": {
                t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in [
                    "receipts",
                    "source_versions",
                    "session_refs",
                    "execution_runs",
                    "answer_tasks",
                    "model_calls",
                ]
            },
        }
    finally:
        db.close()


@pytest.fixture
def scopes(http, owner, config, policy):
    permissions(http, owner[0], owner[2]["personal"], query=True, original=True, add=True)
    receipt = intake(http, owner, body=PRIVATE.encode())
    cid, headers = add_client(http, "synthetic-feishu-no-knowledge-grants")
    vid = owner[2]["personal"]
    version = http.get(f"/v1/vaults/{vid}/versions", headers=owner[1]).json()[0]
    original = f"/v1/vaults/{vid}/versions/{version['id']}/assets/{version['assets'][0]['id']}"
    # A separate client deliberately has no administrator cookie or CSRF secret.
    with TestClient(
        create_app(config, execution_policy=policy),
        base_url="http://127.0.0.1:8765",
        client=("127.0.0.1", 49001),
    ) as caller:
        caller.headers["Origin"] = "http://127.0.0.1:8765"
        submitted = caller.post(
            "/v1/answer-tasks",
            headers=owner[1],
            json={
                "request_id": "weixin-fixed-task",
                "receipt_id": receipt["job_id"],
                "session_id": "current",
                "question": "Evidence?",
                "retrieval_query": PRIVATE,
            },
        )
        assert submitted.status_code == 202
        run = submitted.json()["run_id"]
        assert caller.get(original, headers=owner[1]).content == PRIVATE.encode()
        assert caller.post(
            "/v1/search",
            headers=owner[1],
            json={"query": PRIVATE, "session_id": "current", "vault_ids": [vid]},
        ).json()["results"]
        yield caller, owner, cid, headers, receipt, original, run


def attempt(scopes, action, headers):
    caller, owner, cid, _, receipt, original, run = scopes
    vid = owner[2]["personal"]
    requests = {
        "search": (
            "POST",
            "/v1/search",
            {"query": PRIVATE, "session_id": "current", "vault_ids": [vid]},
        ),
        "answer": (
            "POST",
            "/v1/answers",
            {
                "question": "Evidence?",
                "retrieval_query": PRIVATE,
                "session_id": "current",
                "vault_ids": [vid],
            },
        ),
        "receipt": ("GET", "/v1/receipts/" + receipt["job_id"], None),
        "versions": ("GET", f"/v1/vaults/{vid}/versions", None),
        "original": ("GET", original, None),
        "task_status": ("GET", "/v1/answer-tasks/" + run, None),
        "task_result": ("GET", "/v1/answers/" + run, None),
        "ingest": ("POST", "/v1/receipts", payload(vid)),
        "submit": (
            "POST",
            "/v1/answer-tasks",
            {
                "request_id": "foreign",
                "receipt_id": receipt["job_id"],
                "session_id": "current",
                "question": "Evidence?",
                "retrieval_query": PRIVATE,
            },
        ),
        "submit_notes": (
            "POST",
            "/v1/note-answer-tasks",
            {
                "request_id": "foreign",
                "vault_id": vid,
                "question": "Evidence?",
                "retrieval_query": PRIVATE,
            },
        ),
        "new_vault": ("POST", "/v1/vaults", {"name": "foreign-vault"}),
        "admin_grant": (
            "PUT",
            f"/admin/clients/{cid}/vaults/{vid}",
            {"query": True, "original": True, "add": True},
        ),
        "admin_clients": (
            "POST",
            "/admin/clients",
            {"name": "self-approved", "create_vault": True},
        ),
        "admin_batches": ("GET", "/admin/batches/pending", None),
    }
    method, path, body = requests[action]
    return caller.request(
        method, path, headers=headers, **({"json": body} if body is not None else {})
    )


@pytest.mark.parametrize(
    "action",
    [
        "search",
        "answer",
        "receipt",
        "versions",
        "original",
        "task_status",
        "task_result",
        "ingest",
        "submit",
        "submit_notes",
        "new_vault",
        "admin_grant",
        "admin_clients",
        "admin_batches",
    ],
)
@pytest.mark.parametrize("credential", ["absent", "unrelated", "ungranted"])
def test_platform_and_owner_claims_do_not_inherit_weixin_capabilities(
    scopes, config, credential, action
):
    _, owner, _, feishu_headers, _, _, _ = scopes
    headers = {
        "X-Hermes-Platform": "weixin",
        "X-Client-ID": owner[0],
        "X-Owner-ID": "same-human-on-both-platforms",
        "X-CSRF-Token": "untrusted-feishu-claim",
    }
    if credential == "unrelated":
        headers["Authorization"] = "Bearer synthetic-feishu-platform-token"
    elif credential == "ungranted":
        headers.update(feishu_headers)
    before = snapshot(config)
    with patch.object(
        model_gateway, "complete", side_effect=AssertionError("no provider effects")
    ) as remote:
        response = attempt(scopes, action, headers)
        remote.assert_not_called()
    assert response.status_code in {401, 403, 404}
    assert response.json() == {"error": "request_denied"}
    assert PRIVATE not in response.text and owner[0] not in response.text
    assert snapshot(config) == before


def test_ungranted_feishu_identity_has_no_vaults_or_implicit_grants(scopes, config):
    caller, _, _, headers, _, _, _ = scopes
    before = snapshot(config)
    assert caller.get("/v1/vaults", headers=headers).json() == []
    assert snapshot(config) == before


def test_receiver_install_preserves_separate_feishu_factory_and_context(config, binding, setup):
    home, runner, candidate, pc = setup
    sentinel = object()
    calls = []

    def foreign_factory(platform, settings):
        calls.append((platform, settings))
        return sentinel

    foreign = SimpleNamespace(
        _create_adapter=foreign_factory,
        config=SimpleNamespace(
            platforms={Platform.FEISHU: object()},
            sessions_dir=config.project_root / "feishu-sessions",
        ),
        adapters={Platform.FEISHU: sentinel},
        context={"purpose": "existing-feishu"},
    )
    before = copy.copy(foreign.__dict__)
    original_context = copy.deepcopy(foreign.context)
    original_platforms = dict(foreign.config.platforms)
    original_adapters = dict(foreign.adapters)
    original_sessions = foreign.config.sessions_dir
    grant_snapshot = snapshot(config)
    gateway.install(config, ReceiverConfig(binding, enabled=True), runner, candidate, home=home)
    with pytest.raises(InvalidInput):
        runner._create_adapter(Platform.FEISHU, pc)
    assert foreign.__dict__ == before and not calls
    assert foreign.context == original_context
    assert foreign.config.platforms == original_platforms
    assert foreign.adapters == original_adapters
    assert foreign.config.sessions_dir == original_sessions
    adapter = runner._create_adapter(Platform.WEIXIN, pc)
    assert adapter.gateway_runner is runner and adapter._fiber_inbox == (config, binding)
    assert not hasattr(foreign, "_fiber_receiver_installed")
    assert foreign._create_adapter(Platform.FEISHU, foreign.config) is sentinel
    assert len(calls) == 1 and snapshot(config) == grant_snapshot
