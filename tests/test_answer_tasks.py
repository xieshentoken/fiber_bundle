import json
import sqlite3
from unittest.mock import patch

import pytest
from test_execution import policy as policy
from test_session_search import intake
from test_session_search import owner as owner

from fiber_bundle import answer_tasks, auth, database, execution, model_gateway
from fiber_bundle import execution_ledger as ledger
from fiber_bundle.config import IntegrityError, InvalidInput


@pytest.fixture
def task(http, owner, config, policy):
    receipt = intake(http, owner)
    db = database.connect(config.state_root)
    data = answer_tasks.AnswerInput(receipt["job_id"], "current", "What pressure?", "psi")
    rid = ledger.create_run(
        config,
        db,
        policy,
        owner[0],
        owner[2]["personal"],
        receipt["source_version_id"],
        answer=data,
    )
    db.close()
    return rid, receipt


def response(payload):
    data = json.loads(payload["messages"][1]["content"])
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": json.dumps({"mode": "extractive", "quotations": data["quotations"]}),
                },
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 30},
    }


def invoke(config, policy, rid, remote=None):
    with patch.object(
        model_gateway, "complete", side_effect=remote or (lambda p, r, v: response(v))
    ):
        return execution.handle(
            config,
            policy,
            rid,
            {
                "type": "model",
                "id": "1",
                "body": {
                    "messages": [{"role": "user", "content": "UNTRUSTED PI CONTEXT"}],
                    "tools": [{"evil": True}],
                },
            },
        )


def start(config, policy, rid):
    db = database.connect(config.state_root)
    ledger.start(config, db, rid, policy)
    db.close()


def test_query_only_fixed_input_validated_output_and_http_delivery(
    http, owner, config, policy, task
):
    rid, _ = task
    start(config, policy, rid)

    def remote(p, r, payload):
        assert "UNTRUSTED" not in json.dumps(payload) and "tools" not in payload
        assert "RAW-EVENT-SECRET" not in json.dumps(payload)
        assert payload["max_tokens"] == 512
        return response(payload)

    invoke(config, policy, rid, remote)
    assert http.get("/v1/answers/" + rid, headers=owner[1]).status_code == 422
    db = database.connect(config.state_root)
    ledger.transition(db, rid, "succeeded")
    db.close()
    result = http.get("/v1/answers/" + rid, headers=owner[1])
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    data = result.json()
    assert (
        data["model_used"] is True and data["quotations"][0]["quote"] == "100 psi session evidence"
    )
    assert data["sources"][0]["originals"] == []


@pytest.mark.parametrize("mutation", ["revoke", "cancel", "clear_session", "new_note"])
def test_inflight_change_discards_output_but_keeps_charge(
    http, owner, config, policy, task, mutation
):
    rid, _ = task
    start(config, policy, rid)

    def remote(p, r, payload):
        db = database.connect(config.state_root)
        if mutation == "revoke":
            auth.set_grant(
                db, owner[0], owner[2]["personal"], query=False, original=False, add=True
            )
        elif mutation == "cancel":
            ledger.transition(db, rid, "cancelled")
        elif mutation == "clear_session":
            db.execute("DELETE FROM session_refs")
        else:
            (config.project_root / "test vault/temp_vault1/new.md").write_text("pressure 80 psi")
        db.close()
        return response(payload)

    with pytest.raises((auth.Denied, IntegrityError, InvalidInput)):
        invoke(config, policy, rid, remote)
    db = database.connect(config.state_root)
    assert answer_tasks.get(db, rid)["result_json"] is None
    assert db.execute("SELECT state,charged_nusd FROM model_calls").fetchone()[:] == (
        "settled",
        18000,
    )
    db.close()


@pytest.mark.parametrize(
    "mutation", ["invent", "omit", "unknown", "tool", "truncated", "malformed", "wrong_role"]
)
def test_invalid_provider_output_never_persists_result(http, owner, config, policy, task, mutation):
    rid, _ = task
    start(config, policy, rid)

    def remote(p, r, payload):
        result = response(payload)
        choice = result["choices"][0]
        proposal = json.loads(choice["message"]["content"])
        if mutation == "invent":
            proposal["quotations"][0]["quote"] = "999 psi"
        elif mutation == "omit":
            proposal["quotations"] = []
        elif mutation == "unknown":
            proposal["quotations"][0]["citation"] = "[99]"
        elif mutation == "tool":
            choice["message"]["tool_calls"] = [{"id": "evil"}]
        elif mutation == "wrong_role":
            choice["message"]["role"] = "user"
        elif mutation == "truncated":
            choice["finish_reason"] = "length"
        choice["message"]["content"] = (
            "bad json" if mutation == "malformed" else json.dumps(proposal)
        )
        return result

    with pytest.raises((IntegrityError, InvalidInput)):
        invoke(config, policy, rid, remote)
    db = database.connect(config.state_root)
    assert answer_tasks.get(db, rid)["result_json"] is None
    assert db.execute("SELECT state FROM model_calls").fetchone()[0] == "settled"
    db.close()


def test_delivery_rechecks_note_hash_and_permissions(http, owner, config, policy):
    note = config.project_root / "test vault/temp_vault1/note.md"
    note.write_text("Pressure 80 psi")
    receipt = intake(http, owner)
    db = database.connect(config.state_root)
    rid = ledger.create_run(
        config,
        db,
        policy,
        owner[0],
        owner[2]["personal"],
        receipt["source_version_id"],
        answer=answer_tasks.AnswerInput(receipt["job_id"], "current", "Pressure?", "psi"),
    )
    ledger.start(config, db, rid, policy)
    invoke(config, policy, rid)
    ledger.transition(db, rid, "succeeded")
    assert http.get("/v1/answers/" + rid, headers=owner[1]).status_code == 200
    note.write_text("Pressure 70 psi")
    assert http.get("/v1/answers/" + rid, headers=owner[1]).status_code == 503
    note.write_text("Pressure 80 psi")
    auth.revoke_client(db, owner[0])
    assert http.get("/v1/answers/" + rid, headers=owner[1]).status_code == 401
    db.close()


def test_no_replay_or_runtime_tool_scope(http, owner, config, policy, task):
    rid, _ = task
    start(config, policy, rid)
    with pytest.raises(InvalidInput):
        execution.handle(config, policy, rid, {"type": "vaults", "id": "1", "body": {}})
    invoke(config, policy, rid)
    with patch.object(model_gateway, "complete") as remote:
        with pytest.raises(InvalidInput):
            execution.handle(config, policy, rid, {"type": "model", "id": "2", "body": {}})
        remote.assert_not_called()
    db = database.connect(config.state_root)
    assert db.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 1
    db.close()


def test_wrong_owner_session_or_version_cannot_create(http, owner, config, policy, task):
    rid, receipt = task
    db = database.connect(config.state_root)
    other = auth.create_client(db, "other", False)
    auth.set_grant(db, other["id"], owner[2]["personal"], query=True, original=True, add=False)
    for cid, session in [(other["id"], "current"), (owner[0], "other")]:
        with pytest.raises(auth.Denied):
            ledger.create_run(
                config,
                db,
                policy,
                cid,
                owner[2]["personal"],
                receipt["source_version_id"],
                answer=answer_tasks.AnswerInput(receipt["job_id"], session, "pressure?", "psi"),
            )
    assert db.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0] == 1
    with pytest.raises(auth.Denied):
        answer_tasks.deliver(config, db, other["id"], rid)
    db.close()


def test_no_evidence_empty_selection(http, owner, config, policy):
    receipt = intake(http, owner)
    db = database.connect(config.state_root)
    rid = ledger.create_run(
        config,
        db,
        policy,
        owner[0],
        owner[2]["personal"],
        receipt["source_version_id"],
        answer=answer_tasks.AnswerInput(receipt["job_id"], "current", "Unknown?", "absent"),
    )
    ledger.start(config, db, rid, policy)
    invoke(config, policy, rid)
    ledger.transition(db, rid, "succeeded")
    assert answer_tasks.deliver(config, db, owner[0], rid)["status"] == "insufficient_evidence"
    db.close()


def test_answer_container_has_no_external_network(policy, tmp_path):
    cmd = execution.command(policy, tmp_path, "00000000-0000-4000-8000-000000000000", answer=True)
    assert cmd[cmd.index("--network") + 1] == "none"


def test_v5_migration_backup_and_rollback(config):
    config.state_root.mkdir()
    db = sqlite3.connect(config.state_root / "metadata.sqlite3")
    old = (
        database.SCHEMA
        + database.SCHEMA_V2
        + database.SCHEMA_V3
        + database.SCHEMA_V4
        + database.SCHEMA_V5
    )
    db.executescript(old)
    db.execute("PRAGMA user_version=5")
    db.close()
    with patch.object(database, "SCHEMA_V6", database.SCHEMA_V6 + "BROKEN SQL;"):
        with pytest.raises(sqlite3.Error):
            database.migrate(config)
    with sqlite3.connect(config.state_root / "metadata.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
        assert (
            db.execute("SELECT 1 FROM sqlite_master WHERE name='answer_tasks'").fetchone() is None
        )
    backup = database.migrate(config)
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
    db = database.connect(config.state_root)
    assert db.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    db.close()


def test_original_revocation_rebuilds_links_without_invalidating_query(
    http, owner, config, policy, task
):
    rid, _ = task
    db = database.connect(config.state_root)
    auth.set_grant(db, owner[0], owner[2]["personal"], query=True, original=True, add=True)
    ledger.start(config, db, rid, policy)
    invoke(config, policy, rid)
    ledger.transition(db, rid, "succeeded")
    assert answer_tasks.deliver(config, db, owner[0], rid)["sources"][0]["originals"]
    auth.set_grant(db, owner[0], owner[2]["personal"], query=True, original=False, add=True)
    assert answer_tasks.deliver(config, db, owner[0], rid)["sources"][0]["originals"] == []
    db.close()


def test_stale_snapshot_rejected_before_reserving_or_calling(http, owner, config, policy, task):
    rid, _ = task
    start(config, policy, rid)
    (config.project_root / "test vault/temp_vault1/new.md").write_text("pressure 70 psi")
    with patch.object(model_gateway, "complete") as remote:
        with pytest.raises(IntegrityError):
            execution.handle(config, policy, rid, {"type": "model", "id": "1", "body": {}})
        remote.assert_not_called()
    db = database.connect(config.state_root)
    assert db.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 0
    db.close()


@pytest.mark.parametrize("valid", [True, False])
def test_answer_cli_creates_bound_task_or_returns_safe_error(
    http, owner, config, policy, tmp_path, capsys, valid
):
    from dataclasses import asdict

    from fiber_bundle import answer_cli

    receipt = intake(http, owner)
    token = tmp_path / "client-token"
    token.write_text(
        owner[1]["Authorization"].removeprefix("Bearer ") if valid else "invalid-token"
    )
    question = tmp_path / "question.txt"
    question.write_text("What pressure?")
    execution_config = tmp_path / "execution.json"
    execution_config.write_text(json.dumps(asdict(policy)))
    argv = [
        "fiber-answer",
        "--project-root",
        str(tmp_path),
        "--config",
        "config.json",
        "--execution-config",
        str(execution_config),
        "--token-file",
        str(token),
        "--receipt-id",
        receipt["job_id"],
        "--session-id",
        "current",
        "--question-file",
        str(question),
        "--retrieval-query",
        "psi",
    ]
    with patch("sys.argv", argv):
        code = answer_cli.main()
    output = capsys.readouterr().out
    assert token.read_text() not in output
    result = json.loads(output)
    assert code == (0 if valid else 1)
    db = database.connect(config.state_root)
    assert db.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 0
    if valid:
        assert ledger.get_run(db, result["run_id"])["state"] == "queued"
        assert answer_tasks.get(db, result["run_id"]) is not None
    else:
        assert result["error_code"] == "answer_creation_rejected"
        assert db.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0] == 0
    db.close()
