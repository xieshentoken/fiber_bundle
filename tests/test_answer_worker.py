import json
from dataclasses import asdict, replace
from unittest.mock import patch

import pytest
from test_answer_tasks import task as task
from test_execution import policy as policy
from test_session_search import owner as owner

from fiber_bundle import answer_tasks, answer_worker, answer_worker_cli, auth, database, execution
from fiber_bundle import execution_ledger as ledger
from fiber_bundle.config import InvalidInput


def create(config, policy, owner, receipt):
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
    db.close()
    return rid


def finish(config, policy, rid, prompt):
    db = database.connect(config.state_root)
    ledger.start(config, db, rid, policy)
    ledger.transition(db, rid, "succeeded")
    db.close()
    return {"run_id": rid, "state": "succeeded", "error_code": None}


def test_fifo_bounded_batch_and_idle(http, owner, config, policy, task):
    first, receipt = task
    second = create(config, policy, owner, receipt)
    with patch.object(execution, "execute", side_effect=finish) as execute:
        result = answer_worker.dispatch(config, policy)
        assert result["state"] == "limit_reached" and result["processed"] == 1
        assert result["results"][0]["run_id"] == first
        assert execute.call_count == 1
        result = answer_worker.dispatch(config, policy, max_tasks=2)
        assert result["state"] == "idle" and result["processed"] == 1
        assert result["results"][0]["run_id"] == second


@pytest.mark.parametrize(
    "state", ["running", "waiting_budget", "failed", "cancelled", "interrupted", "succeeded"]
)
def test_never_automatically_retries_nonqueued_tasks(config, policy, task, state):
    db = database.connect(config.state_root)
    ledger.transition(db, task[0], state)
    db.close()
    with patch.object(execution, "execute") as execute:
        assert answer_worker.dispatch(config, policy)["state"] == "idle"
        execute.assert_not_called()


def test_prior_model_effects_and_wrong_policy_are_not_dispatched(config, policy, task):
    with patch.object(execution, "execute") as execute:
        assert (
            answer_worker.dispatch(config, replace(policy, max_output_tokens=256))["state"]
            == "idle"
        )
        db = database.connect(config.state_root)
        ledger.start(config, db, task[0], policy)
        ledger.reserve(config, db, task[0], policy)
        ledger.transition(db, task[0], "queued")
        db.close()
        assert answer_worker.dispatch(config, policy)["state"] == "idle"
        execute.assert_not_called()


@pytest.mark.parametrize("change", ["revoke", "clear_session", "edit"])
def test_preflight_blocked_without_starting_or_charging(config, policy, task, owner, change):
    db = database.connect(config.state_root)
    if change == "revoke":
        auth.revoke_client(db, owner[0])
    elif change == "clear_session":
        db.execute("DELETE FROM session_refs")
    else:
        (config.project_root / "test vault/temp_vault1/new.md").write_text("pressure 90 psi")
    with patch.object(execution, "execute") as execute:
        result = answer_worker.dispatch(config, policy)
        assert result["state"] == "blocked" and result["run_id"] == task[0]
        assert result["error_code"] == "answer_preflight_rejected"
        execute.assert_not_called()
    assert ledger.get_run(db, task[0])["state"] == "queued"
    assert db.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 0
    db.close()


def test_worker_lock_excludes_second_dispatcher(config, policy, task):
    with answer_worker.worker_lock(config):
        with pytest.raises(InvalidInput):
            answer_worker.dispatch(config, policy)
    with answer_worker.worker_lock(config):
        pass


def test_failure_stops_batch_without_consuming_next(config, policy, task, owner):
    second = create(config, policy, owner, task[1])

    def failure(c, p, rid, prompt):
        db = database.connect(c.state_root)
        ledger.start(c, db, rid, p)
        ledger.transition(db, rid, "waiting_budget", "budget_exhausted")
        db.close()
        return {"run_id": rid, "state": "waiting_budget", "error_code": "budget_exhausted"}

    with patch.object(execution, "execute", side_effect=failure) as execute:
        result = answer_worker.dispatch(config, policy, max_tasks=2)
        assert result["state"] == "stopped" and result["processed"] == 1
        assert execute.call_count == 1
    db = database.connect(config.state_root)
    assert ledger.get_run(db, second)["state"] == "queued"
    db.close()


def test_manual_executor_race_is_not_retried(config, policy, task):
    with patch.object(execution, "execute", side_effect=InvalidInput("already owned")) as execute:
        result = answer_worker.dispatch(config, policy)
        assert result["state"] == "blocked" and result["error_code"] == "answer_dispatch_rejected"
        assert execute.call_count == 1


@pytest.mark.parametrize("limit", [0, 101, True])
def test_worker_limit_must_be_explicit_and_bounded(config, policy, task, limit):
    with pytest.raises(InvalidInput):
        answer_worker.dispatch(config, policy, max_tasks=limit)


def test_worker_cli_only_reports_metadata(config, policy, task, tmp_path, capsys):
    p = tmp_path / "execution.json"
    p.write_text(json.dumps(asdict(policy)))
    argv = [
        "fiber-answer-worker",
        "--project-root",
        str(tmp_path),
        "--config",
        "config.json",
        "--execution-config",
        str(p),
    ]
    with patch("sys.argv", argv), patch.object(execution, "execute", side_effect=finish):
        assert answer_worker_cli.main() == 0
    result = capsys.readouterr().out
    assert policy.secret_file not in result and "Pressure?" not in result
    assert json.loads(result)["processed"] == 1


def test_generic_execution_is_not_picked_by_answer_worker(config, policy, task, owner):
    db = database.connect(config.state_root)
    ledger.transition(db, task[0], "cancelled")
    auth.set_grant(db, owner[0], owner[2]["personal"], query=True, original=True, add=True)
    other = ledger.create_run(
        config, db, policy, owner[0], owner[2]["personal"], task[1]["source_version_id"]
    )
    with patch.object(execution, "execute") as execute:
        assert answer_worker.dispatch(config, policy)["state"] == "idle"
        execute.assert_not_called()
    assert ledger.get_run(db, other)["state"] == "queued"
    db.close()
