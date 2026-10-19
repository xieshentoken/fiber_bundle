"""Persistent multi-Vault answers preserve the submitted scope across execution and retries."""

import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from test_answer_submissions import enabled as enabled
from test_answer_tasks import invoke, response, start
from test_api import add_client, permissions
from test_execution import policy as policy
from test_session_search import owner as owner

from fiber_bundle import answer_tasks, answer_worker, auth, database, execution_ledger, note_index
from fiber_bundle.api import create_app
from fiber_bundle.config import IntegrityError

ENDPOINT = "/v1/scoped-note-answer-tasks"


@pytest.fixture
def notes(config, http, owner):
    for name, relative in config.vaults:
        permissions(http, owner[0], owner[2][name], query=True)
        (config.project_root / relative / "evidence.md").write_text(f"{name} pressure 100 psi.")
    return owner


def submit(client, owner, **extra):
    return client.post(
        ENDPOINT,
        headers=owner[1],
        json={
            "request_id": "scoped-1",
            "question": "What pressure?",
            "retrieval_query": "pressure",
            **extra,
        },
    )


def defaults(http, owner, names):
    for name, vid in owner[2].items():
        assert (
            http.patch("/admin/vaults/" + vid, json={"default_query": name in names}).status_code
            == 200
        )


def test_two_vaults_to_model_and_delivery_with_one_shared_limit(enabled, notes, config, policy):
    ids = list(notes[2].values())
    created = submit(enabled, notes, vault_ids=ids)
    assert created.status_code == 202
    rid = created.json()["run_id"]
    assert created.json()["vault_ids"] == ids and created.json()["scope_mode"] == "explicit"
    start(config, policy, rid)

    def provider(p, r, payload):
        content = json.loads(payload["messages"][1]["content"])
        assert {q["quote"] for q in content["quotations"]} == {
            "personal pressure 100 psi.",
            "supplier pressure 100 psi.",
        }
        assert "vault_ids" not in json.dumps(payload)
        return response(payload)

    invoke(config, policy, rid, provider)
    db = database.connect(config.state_root)
    execution_ledger.transition(db, rid, "succeeded")
    data = enabled.get("/v1/answers/" + rid, headers=notes[1]).json()
    assert data["searched_vault_ids"] == ids
    assert {s["vault_id"] for s in data["sources"]} == set(ids)
    assert [q["citation"] for q in data["quotations"]] == ["[1]", "[2]"]
    limited = submit(enabled, notes, vault_ids=ids, request_id="limited", limit=1)
    task = answer_tasks.get(db, limited.json()["run_id"])
    assert len(json.loads(task["snapshot_json"])["sources"]) == 1
    for table in ["receipts", "source_versions"]:
        assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    db.close()


def test_default_freezes_membership_and_reuses_task_after_restart(
    enabled, notes, config, policy, http
):
    defaults(http, notes, ["personal"])
    first = submit(enabled, notes)
    assert first.status_code == 202 and first.json()["vault_ids"] == [notes[2]["personal"]]
    defaults(http, notes, ["supplier"])
    with TestClient(
        create_app(config, execution_policy=policy),
        base_url="http://127.0.0.1:8765",
        client=("127.0.0.1", 49000),
    ) as restarted:
        assert submit(restarted, notes).json() == first.json()
        fresh = submit(restarted, notes, request_id="new-default")
        assert fresh.json()["vault_ids"] == [notes[2]["supplier"]]
    db = database.connect(config.state_root)
    rid = first.json()["run_id"]
    current = answer_tasks.current(
        config, db, execution_ledger.get_run(db, rid), answer_tasks.get(db, rid)
    )
    assert current["searched_vault_ids"] == [notes[2]["personal"]]
    db.close()


@pytest.mark.parametrize("explicit", [False, True])
def test_empty_scope_refuses_before_read_or_task_creation(enabled, notes, config, explicit):
    with patch.object(note_index, "snapshot", side_effect=AssertionError("no note read")):
        result = submit(enabled, notes, **({"vault_ids": []} if explicit else {}))
    assert result.status_code == 422
    db = database.connect(config.state_root)
    assert db.execute("SELECT count(*) FROM execution_runs").fetchone()[0] == 0
    db.close()


def test_all_targets_authorized_before_any_content_read(enabled, notes, http):
    permissions(http, notes[0], notes[2]["supplier"], query=False)
    with patch.object(note_index, "snapshot", side_effect=AssertionError("no partial reads")):
        assert submit(enabled, notes, vault_ids=list(notes[2].values())).status_code == 404
    defaults(http, notes, ["personal", "supplier"])
    assert submit(enabled, notes).json()["vault_ids"] == [notes[2]["personal"]]


@pytest.mark.parametrize("mutation", ["revoke", "edit", "delete"])
def test_secondary_vault_change_blocks_worker_before_call(
    enabled, notes, config, policy, http, mutation
):
    created = submit(enabled, notes, vault_ids=list(notes[2].values()))
    if mutation == "revoke":
        permissions(http, notes[0], notes[2]["supplier"], query=False)
    else:
        note = config.project_root / "test vault/temp_vault2/evidence.md"
        if mutation == "edit":
            note.write_text("supplier pressure 80 psi.")
        else:
            note.unlink()
    with patch("fiber_bundle.execution.execute", side_effect=AssertionError("no execution")):
        result = answer_worker.dispatch(config, policy)
    assert result["state"] == "blocked"
    db = database.connect(config.state_root)
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    assert execution_ledger.get_run(db, created.json()["run_id"])["state"] == "queued"
    db.close()


def test_revoke_non_hit_scope_during_model_retains_cost_but_no_result(
    enabled, notes, config, policy, http
):
    (config.project_root / "test vault/temp_vault2/evidence.md").write_text("unrelated")
    rid = submit(enabled, notes, vault_ids=list(notes[2].values())).json()["run_id"]
    start(config, policy, rid)

    def provider(p, r, payload):
        db = database.connect(config.state_root)
        auth.set_grant(db, notes[0], notes[2]["supplier"], query=False, original=False, add=False)
        db.close()
        return response(payload)

    with pytest.raises(auth.Denied):
        invoke(config, policy, rid, provider)
    db = database.connect(config.state_root)
    assert answer_tasks.get(db, rid)["result_json"] is None
    assert db.execute("SELECT state FROM model_calls").fetchone()[0] == "settled"
    assert enabled.get("/v1/answers/" + rid, headers=notes[1]).status_code == 404
    assert submit(enabled, notes, vault_ids=list(notes[2].values())).status_code == 404
    db.close()


def test_conflicts_and_concurrent_submissions(enabled, notes):
    ids = list(notes[2].values())
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: submit(enabled, notes, vault_ids=ids), range(3)))
    assert all(r.status_code == 202 for r in results)
    assert len({r.json()["run_id"] for r in results}) == 1
    assert submit(enabled, notes, vault_ids=ids[:1]).status_code == 409
    assert submit(enabled, notes).status_code == 409
    assert (
        enabled.post(
            "/v1/note-answer-tasks",
            headers=notes[1],
            json={
                "request_id": "scoped-1",
                "vault_id": ids[0],
                "question": "What pressure?",
                "retrieval_query": "pressure",
            },
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"vault_ids": ["x", "x"]},
        {"vault_ids": ["x"] * 21},
        {"session_id": "current"},
        {"model": "other"},
    ],
)
def test_invalid_scoped_payload(enabled, notes, extra):
    assert submit(enabled, notes, **extra).status_code == 422


def test_owner_isolation_and_policy_disabled(http, enabled, notes):
    assert submit(http, notes, vault_ids=list(notes[2].values())).status_code == 503
    rid = submit(enabled, notes, vault_ids=list(notes[2].values())).json()["run_id"]
    _, other = add_client(http, "other")
    assert enabled.get("/v1/answer-tasks/" + rid, headers=other).status_code == 404


def test_malformed_persisted_scope_never_falls_back_to_primary(enabled, notes, config):
    rid = submit(enabled, notes, vault_ids=list(notes[2].values())).json()["run_id"]
    db = database.connect(config.state_root)
    task = answer_tasks.get(db, rid)
    request = json.loads(task["request_json"])
    del request["vault_ids"]
    db.execute("UPDATE answer_tasks SET request_json=? WHERE run_id=?", (json.dumps(request), rid))
    with pytest.raises(IntegrityError):
        answer_tasks.current(
            config, db, execution_ledger.get_run(db, rid), answer_tasks.get(db, rid)
        )
    db.close()


@pytest.mark.parametrize("mode", ["explicit", "default"])
def test_scoped_cli_persists_resolved_membership(
    notes, config, policy, http, tmp_path, capsys, mode
):
    from dataclasses import asdict

    from fiber_bundle import answer_cli

    defaults(http, notes, ["personal", "supplier"])
    settings = tmp_path / "execution.json"
    settings.write_text(json.dumps(asdict(policy)))
    token = tmp_path / "token"
    token.write_text(notes[1]["Authorization"].removeprefix("Bearer "))
    question = tmp_path / "question"
    question.write_text("What pressure?")
    argv = [
        "fiber-answer",
        "--project-root",
        str(tmp_path),
        "--config",
        "config.json",
        "--execution-config",
        str(settings),
        "--token-file",
        str(token),
        "--question-file",
        str(question),
        "--retrieval-query",
        "pressure",
    ]
    argv += ["--default-vaults"] if mode == "default" else ["--vault-ids", *notes[2].values()]
    with patch("sys.argv", argv):
        assert answer_cli.main() == 0
    rid = json.loads(capsys.readouterr().out)["run_id"]
    db = database.connect(config.state_root)
    stored = json.loads(answer_tasks.get(db, rid)["request_json"])
    assert set(stored["vault_ids"]) == set(notes[2].values())
    assert (stored["scope_request"] is None) == (mode == "default")
    db.close()


def test_scoped_snapshot_cannot_match_legacy_single_vault_snapshot(enabled, notes, config):
    (config.project_root / "test vault/temp_vault2/evidence.md").write_text("unrelated")
    ids = list(notes[2].values())
    rid = submit(enabled, notes, vault_ids=ids).json()["run_id"]
    db = database.connect(config.state_root)
    run = execution_ledger.get_run(db, rid)
    task = answer_tasks.get(db, rid)
    current = answer_tasks.current(config, db, run, task)
    stored = json.loads(task["snapshot_json"])
    assert stored["schema"] == "scoped-note-evidence/v1"
    assert stored["vault_ids"] == ids
    assert len(stored["sources"]) == 1
    assert answer_tasks.snapshot(current) != stored  # Legacy worker's bare-list comparison.
    request = json.loads(task["request_json"])
    request["scope_request"] = request["vault_ids"] = ids[:1]
    db.execute("UPDATE answer_tasks SET request_json=? WHERE run_id=?", (json.dumps(request), rid))
    with pytest.raises(IntegrityError):
        answer_tasks.current(config, db, run, answer_tasks.get(db, rid))
    db.close()
