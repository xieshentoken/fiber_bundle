import json
from dataclasses import asdict
from unittest.mock import patch

import pytest
from test_answer_submissions import enabled as enabled
from test_answer_tasks import invoke, start
from test_api import add_client, permissions
from test_execution import policy as policy
from test_session_search import intake
from test_session_search import owner as owner

from fiber_bundle import answer_cli, answer_tasks, auth, database, execution_ledger
from fiber_bundle.config import IntegrityError


@pytest.fixture
def note(config, owner, http):
    permissions(http, owner[0], owner[2]["personal"], query=True)
    path = config.project_root / "test vault/temp_vault1/pressure.md"
    path.write_text("Synthetic pressure 100 psi.")
    return path


def submit(enabled, owner, **extra):
    return enabled.post(
        "/v1/note-answer-tasks",
        headers=owner[1],
        json={
            "request_id": "note-question",
            "vault_id": owner[2]["personal"],
            "question": "What pressure?",
            "retrieval_query": "pressure",
            **extra,
        },
    )


def test_query_only_notes_model_roundtrip_without_sources(enabled, owner, config, policy, note):
    created = submit(enabled, owner)
    assert created.status_code == 202
    assert submit(enabled, owner).json() == created.json()
    rid = created.json()["run_id"]
    start(config, policy, rid)
    invoke(config, policy, rid)
    db = database.connect(config.state_root)
    execution_ledger.transition(db, rid, "succeeded")
    result = enabled.get("/v1/answers/" + rid, headers=owner[1])
    assert result.status_code == 200
    assert result.json()["quotations"] == [{"citation": "[1]", "quote": note.read_text()}]
    assert result.json()["model_used"] is True
    assert db.execute("SELECT binding_kind,version_id FROM execution_runs").fetchone()[:] == (
        "notes",
        None,
    )
    assert db.execute("SELECT input_kind,receipt_id FROM answer_tasks").fetchone()[:] == (
        "notes",
        None,
    )
    for table in ("sources", "source_versions", "receipts", "assets", "session_refs"):
        assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert db.execute("SELECT state FROM model_calls").fetchone()[0] == "settled"
    db.close()


@pytest.mark.parametrize("mutation", ["edit", "delete", "revoke", "disable"])
def test_changed_notes_or_authority_refuse_model_call(
    enabled, owner, config, policy, note, mutation
):
    rid = submit(enabled, owner).json()["run_id"]
    db = database.connect(config.state_root)
    if mutation == "edit":
        note.write_text("Synthetic pressure 90 psi.")
    elif mutation == "delete":
        note.unlink()
    elif mutation == "revoke":
        auth.set_grant(db, owner[0], owner[2]["personal"], query=False, original=False, add=False)
    else:
        db.execute("UPDATE clients SET active=0 WHERE id=?", (owner[0],))
    with pytest.raises((auth.Denied, IntegrityError)):
        answer_tasks.model_request(
            config, db, execution_ledger.get_run(db, rid), answer_tasks.get(db, rid)
        )
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    db.close()


def test_delivery_rechecks_note_snapshot_and_revocation(enabled, owner, config, policy, note):
    rid = submit(enabled, owner).json()["run_id"]
    start(config, policy, rid)
    invoke(config, policy, rid)
    db = database.connect(config.state_root)
    execution_ledger.transition(db, rid, "succeeded")
    original = note.read_text()
    note.write_text("Changed pressure 90 psi.")
    assert enabled.get("/v1/answers/" + rid, headers=owner[1]).status_code == 503
    note.write_text(original)
    assert enabled.get("/v1/answers/" + rid, headers=owner[1]).status_code == 200
    auth.set_grant(db, owner[0], owner[2]["personal"], query=False, original=False, add=False)
    assert enabled.get("/v1/answers/" + rid, headers=owner[1]).status_code == 404
    db.close()


def test_scope_owner_and_cross_kind_idempotence(enabled, http, owner, config, note):
    rid = submit(enabled, owner).json()["run_id"]
    assert submit(enabled, owner, question="Different?").status_code == 409
    assert submit(enabled, owner, vault_id=owner[2]["supplier"]).status_code == 404
    permissions(http, owner[0], owner[2]["supplier"], query=True)
    assert submit(enabled, owner, vault_id=owner[2]["supplier"]).status_code == 409
    other, headers = add_client(http, "other")
    assert enabled.get("/v1/answer-tasks/" + rid, headers=headers).status_code == 404
    permissions(http, owner[0], owner[2]["personal"], query=True, add=True)
    receipt = intake(http, owner)
    assert (
        enabled.post(
            "/v1/answer-tasks",
            headers=owner[1],
            json={
                "request_id": "note-question",
                "receipt_id": receipt["job_id"],
                "session_id": "current",
                "question": "What pressure?",
                "retrieval_query": "pressure",
            },
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "extra", [{"session_id": "current"}, {"receipt_id": "fake"}, {"model": "other"}, {"limit": 21}]
)
def test_note_api_rejects_ambiguous_scope_and_policy(enabled, owner, note, extra):
    assert submit(enabled, owner, **extra).status_code == 422


def test_note_cli_creates_without_receipt(config, policy, owner, note, tmp_path, capsys):
    token = tmp_path / "token"
    token.write_text(owner[1]["Authorization"].removeprefix("Bearer "))
    settings = tmp_path / "policy.json"
    settings.write_text(json.dumps(asdict(policy)))
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
        "--vault-id",
        owner[2]["personal"],
        "--question-file",
        str(question),
        "--retrieval-query",
        "pressure",
    ]
    with patch("sys.argv", argv):
        assert answer_cli.main() == 0
    rid = json.loads(capsys.readouterr().out)["run_id"]
    db = database.connect(config.state_root)
    assert answer_tasks.get(db, rid)["input_kind"] == "notes"
    db.close()
    with patch("sys.argv", [*argv, "--session-id", "current"]), pytest.raises(SystemExit):
        answer_cli.main()
