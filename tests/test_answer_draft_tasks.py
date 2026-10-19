"""Opt-in draft execution preserves authorization, accounting and host uncertainty labels."""

import json
import sys
from dataclasses import asdict
from unittest.mock import patch

import pytest
from test_answer_submissions import enabled as enabled
from test_answer_tasks import invoke, response, start
from test_execution import policy as policy
from test_session_search import intake
from test_session_search import owner as owner

from fiber_bundle import (
    answer_cli,
    answer_tasks,
    auth,
    database,
    execution,
    execution_ledger,
    model_gateway,
)
from fiber_bundle.answer_drafts import DraftRejected
from fiber_bundle.config import IntegrityError


def proposal(payload):
    data = json.loads(payload["messages"][1]["content"])
    quotes = data["quotations"]
    return {
        "mode": "cited_draft",
        "quotations": quotes,
        "claims": [
            {
                "text": "资料记录了压力值，应结合条件核对。",
                "citations": [q["citation"] for q in quotes],
            }
        ]
        if quotes
        else [],
    }


def draft_response(payload):
    body = response(payload)
    body["choices"][0]["message"]["content"] = json.dumps(proposal(payload))
    return body


@pytest.fixture
def submitted(config, http, enabled, owner):
    (config.project_root / "test vault/temp_vault1/pressure.md").write_text("pressure 100 psi")

    def create(kind="notes", mode="cited_draft", request_id="draft-1", query="pressure"):
        data = {
            "request_id": request_id,
            "question": "Pressure?",
            "retrieval_query": query,
            "mode": mode,
        }
        if kind == "receipt":
            receipt = intake(http, owner)
            data.update(receipt_id=receipt["job_id"], session_id="current")
            endpoint = "/v1/answer-tasks"
        elif kind == "scoped":
            data["vault_ids"] = [owner[2]["personal"]]
            endpoint = "/v1/scoped-note-answer-tasks"
        else:
            data["vault_id"] = owner[2]["personal"]
            endpoint = "/v1/note-answer-tasks"
        return enabled.post(endpoint, headers=owner[1], json=data)

    return create


@pytest.mark.parametrize("kind", ["receipt", "notes", "scoped"])
def test_explicit_draft_roundtrip_and_persisted_contract(
    config, policy, owner, enabled, submitted, kind
):
    created = submitted(kind)
    assert created.status_code == 202 and created.json()["mode"] == "cited_draft"
    rid = created.json()["run_id"]
    start(config, policy, rid)

    def provider(p, r, payload):
        assert 'mode="cited_draft"' in payload["messages"][0]["content"]
        assert "UNTRUSTED PI CONTEXT" not in json.dumps(payload)
        assert "tools" not in payload
        return draft_response(payload)

    invoke(config, policy, rid, provider)
    db = database.connect(config.state_root)
    task = answer_tasks.get(db, rid)
    assert json.loads(task["request_json"])["mode"] == "cited_draft"
    assert json.loads(task["snapshot_json"])["schema"] == "answer-mode/v1"
    stored = json.loads(task["result_json"])
    assert set(stored) == {"mode", "claims", "quotations"}
    execution_ledger.transition(db, rid, "succeeded")
    db.close()
    result = enabled.get("/v1/answers/" + rid, headers=owner[1])
    assert result.status_code == 200
    data = result.json()
    assert data["mode"] == "cited_draft" and data["status"] == "unverified_draft"
    assert data["answer"].startswith("模型草稿，语义未核验。")
    assert data["claims"][0]["semantic_support"] == "not_verified"
    assert data["semantic_sufficiency"] == data["freshness_assessment"] == "not_assessed"
    assert len(data["sources"]) == len(data["quotations"])
    assert data["model_used"] is True and data["sources"][0]["source_label"]


def test_mode_changes_conflict_and_explicit_extractive_matches_default(
    submitted, config, enabled, owner
):
    first = submitted()
    assert submitted().json() == first.json()
    assert submitted(mode="extractive").status_code == 409
    exact = submitted(mode="extractive", request_id="exact")
    assert exact.status_code == 202
    db = database.connect(config.state_root)
    assert "mode" not in json.loads(answer_tasks.get(db, exact.json()["run_id"])["request_json"])
    db.close()
    assert (
        enabled.post(
            "/v1/note-answer-tasks",
            headers=owner[1],
            json={
                "request_id": "exact",
                "question": "Pressure?",
                "retrieval_query": "pressure",
                "vault_id": owner[2]["personal"],
            },
        ).json()
        == exact.json()
    )


@pytest.mark.parametrize("mutation", ["revoke", "edit", "cancel"])
def test_draft_inflight_changes_discard_result_keep_charge(
    submitted, config, policy, owner, mutation
):
    rid = submitted().json()["run_id"]
    start(config, policy, rid)

    def provider(p, r, payload):
        db = database.connect(config.state_root)
        if mutation == "revoke":
            auth.set_grant(
                db, owner[0], owner[2]["personal"], query=False, original=False, add=False
            )
        elif mutation == "cancel":
            execution_ledger.transition(db, rid, "cancelled")
        else:
            (config.project_root / "test vault/temp_vault1/pressure.md").write_text(
                "pressure 90 psi"
            )
        db.close()
        return draft_response(payload)

    from fiber_bundle.config import InvalidInput

    with pytest.raises((auth.Denied, IntegrityError, InvalidInput)):
        invoke(config, policy, rid, provider)
    db = database.connect(config.state_root)
    assert answer_tasks.get(db, rid)["result_json"] is None
    assert db.execute("SELECT state FROM model_calls").fetchone()[0] == "settled"
    db.close()


def test_empty_evidence_draft_abstains(submitted, config, policy, owner, enabled):
    rid = submitted(query="missing").json()["run_id"]
    start(config, policy, rid)
    invoke(config, policy, rid, lambda p, r, payload: draft_response(payload))
    db = database.connect(config.state_root)
    execution_ledger.transition(db, rid, "succeeded")
    db.close()
    result = enabled.get("/v1/answers/" + rid, headers=owner[1]).json()
    assert result["claims"] == result["quotations"] == []
    assert result["status"] == "insufficient_evidence"


def test_output_cannot_switch_to_extractive_for_draft(submitted, config, policy):
    rid = submitted().json()["run_id"]
    start(config, policy, rid)
    with pytest.raises(DraftRejected, match="schema"):
        invoke(config, policy, rid)


def test_mode_tampering_invalidates_snapshot(submitted, config):
    rid = submitted().json()["run_id"]
    db = database.connect(config.state_root)
    task = answer_tasks.get(db, rid)
    data = json.loads(task["request_json"])
    data.pop("mode")
    db.execute("UPDATE answer_tasks SET request_json=? WHERE run_id=?", (json.dumps(data), rid))
    with pytest.raises(IntegrityError):
        answer_tasks.current(
            config, db, execution_ledger.get_run(db, rid), answer_tasks.get(db, rid)
        )
    db.close()


def test_draft_rejection_persists_safe_error_code(submitted, config, policy, tmp_path):
    rid = submitted().json()["run_id"]
    runtime = tmp_path / "runtime.py"
    runtime.write_text(
        "import json,sys\njson.loads(sys.stdin.readline())\n"
        'print(json.dumps({"type":"model","id":"1","body":{}}),flush=True)\n'
        "sys.stdin.readline()\n"
    )

    def provider(p, r, payload):
        result = draft_response(payload)
        value = proposal(payload)
        value["claims"][0]["citations"] = ["[99]"]
        result["choices"][0]["message"]["content"] = json.dumps(value)
        return result

    with (
        patch.object(execution, "command", return_value=[sys.executable, str(runtime)]),
        patch.object(execution, "stop"),
        patch.object(model_gateway, "complete", side_effect=provider),
    ):
        result = execution.execute(config, policy, rid, "")
    assert (
        result["state"] == "failed"
        and result["error_code"] == "answer_draft_rejected_unknown_citation"
    )
    db = database.connect(config.state_root)
    assert answer_tasks.get(db, rid)["result_json"] is None
    assert db.execute("SELECT state FROM model_calls").fetchone()[0] == "settled"
    db.close()


def test_cli_opt_in_persists_mode(config, policy, owner, submitted, tmp_path, capsys):
    settings = tmp_path / "policy.json"
    settings.write_text(json.dumps(asdict(policy)))
    token = tmp_path / "token"
    token.write_text(owner[1]["Authorization"].removeprefix("Bearer "))
    question = tmp_path / "question"
    question.write_text("Pressure?")
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
        "--vault-id",
        owner[2]["personal"],
        "--mode",
        "cited_draft",
    ]
    with patch("sys.argv", argv):
        assert answer_cli.main() == 0
    rid = json.loads(capsys.readouterr().out)["run_id"]
    db = database.connect(config.state_root)
    assert json.loads(answer_tasks.get(db, rid)["request_json"])["mode"] == "cited_draft"
    db.close()
