"""Bounded local task scheduling over real isolated journals; no external provider or Docker."""

import json
import sys
from dataclasses import replace
from unittest.mock import patch

import pytest
from test_answer_tasks import response
from test_execution import policy as policy
from test_weixin_inbox import binding as binding
from test_weixin_store import main_rows
from test_weixin_task_bindings import setup
from test_weixin_task_inputs import CAPTURE, saved

from fiber_bundle import answer_tasks, auth, database, execution, model_gateway
from fiber_bundle import execution_ledger as ledger
from fiber_bundle import weixin_batches as batches
from fiber_bundle import weixin_inbox as inbox
from fiber_bundle import weixin_task_bindings as bindings
from fiber_bundle import weixin_task_dispatch as worker
from fiber_bundle import weixin_task_status as status
from fiber_bundle.config import InvalidInput


def queued(config, binding, policy):
    bid = setup(config, binding)
    return bid, bindings.submit(config, binding, bid, policy)["run_id"]


def two_articles(config, binding, policy):
    bid, receipts = saved(
        config, binding, "save_separately", ["https://x.com/a/status/1", "https://x.com/b/status/2"]
    )
    return bid, [
        bindings.submit(
            config, binding, bid, policy, capture_policy=CAPTURE, parent_receipt_id=r["job_id"]
        )["run_id"]
        for r in receipts["receipts"]
    ]


def finish(config, policy, run_id, prompt):
    # Scheduler-only fixture; real executor/answer validation exercised separately below.
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            ledger.start(config, db, run_id, policy)
            ledger.transition(db, run_id, "succeeded")
        finally:
            db.close()
    return {"state": "succeeded"}


def test_disabled_has_no_side_effects(config, policy):
    assert worker.dispatch(config, None, policy)["state"] == "disabled"
    assert not config.state_root.exists()


@pytest.mark.parametrize("maximum", [-1, 101, True, 1.5, "1"])
def test_invalid_limits(config, policy, maximum):
    with pytest.raises(InvalidInput):
        worker.dispatch(config, None, policy, maximum=maximum)


def test_fifo_limit_and_no_replay(config, binding, policy):
    _, runs = two_articles(config, binding, policy)
    with patch.object(execution, "execute", side_effect=finish) as execute:
        first = worker.dispatch(config, binding, policy, maximum=1)
        assert first["state"] == "limit_reached" and first["processed"] == 1
        assert first["results"][0]["run_id"] == runs[0]
        second = worker.dispatch(config, binding, policy, maximum=2)
        assert second["state"] == "idle" and second["processed"] == 1
        assert second["results"][0]["run_id"] == runs[1]
        assert worker.dispatch(config, binding, policy, maximum=2)["processed"] == 0
        assert execute.call_count == 2
    assert status.synchronize(config, binding, runs[0])["dispatch_phase"] == "finished"


def test_wrong_policy_and_account_do_not_execute(config, binding, policy):
    _, run_id = queued(config, binding, policy)
    other = replace(binding, account_id="other-account")
    inbox.initialize(config, other)
    with patch.object(execution, "execute") as execute:
        assert worker.dispatch(config, other, policy, maximum=1)["state"] == "idle"
        assert (
            worker.dispatch(config, binding, replace(policy, max_attempts=3), maximum=1)["state"]
            == "idle"
        )
        with pytest.raises(auth.Denied):
            status.synchronize(config, other, run_id)
        execute.assert_not_called()
    with pytest.raises(InvalidInput):
        status.synchronize(config, replace(binding, owner_id="forged-owner"), run_id)


@pytest.mark.parametrize(
    "state", ["failed", "running", "waiting_budget", "interrupted", "cancelled", "succeeded"]
)
def test_nonqueued_never_automatically_replayed(config, binding, policy, state):
    _, run_id = queued(config, binding, policy)
    db = database.connect(config.state_root)
    ledger.transition(db, run_id, state)
    db.close()
    with patch.object(execution, "execute") as execute:
        assert worker.dispatch(config, binding, policy, maximum=1)["state"] == "idle"
        execute.assert_not_called()
    assert status.synchronize(config, binding, run_id)["run_state"] == state


def test_worker_and_native_run_locks(config, binding, policy):
    _, run_id = queued(config, binding, policy)
    with worker.worker_lock(config):
        with pytest.raises(InvalidInput, match="already active"):
            worker.dispatch(config, binding, policy, maximum=1)
    with execution.run_lock(config, run_id):
        with pytest.raises(InvalidInput, match="already owned"):
            worker.reconcile(config, binding, run_id)


@pytest.mark.parametrize("completed", [False, True])
def test_interruption_claim_survives_and_reconcile_never_replays(
    config, binding, policy, completed
):
    _, run_id = queued(config, binding, policy)

    def interrupted(*args):
        if completed:
            finish(*args)
        raise KeyboardInterrupt()

    with patch.object(execution, "execute", side_effect=interrupted):
        with pytest.raises(KeyboardInterrupt):
            worker.dispatch(config, binding, policy, maximum=1)
    assert status.synchronize(config, binding, run_id)["dispatch_phase"] == "claimed"
    with patch.object(execution, "execute") as execute:
        assert worker.dispatch(config, binding, policy, maximum=1)["state"] == "blocked"
        view = worker.reconcile(config, binding, run_id)
        assert view["dispatch_phase"] == ("finished" if completed else "unknown")
        assert worker.dispatch(config, binding, policy, maximum=1)["state"] == (
            "idle" if completed else "blocked"
        )
        execute.assert_not_called()


def test_failure_stops_remaining_and_exception_stays_unresolved(config, binding, policy):
    _, runs = two_articles(config, binding, policy)

    def fail(config, policy, run_id, prompt):
        db = database.connect(config.state_root)
        ledger.transition(db, run_id, "failed", "synthetic_failure")
        db.close()
        return {"state": "succeeded"}  # untrusted return must not override persistent state

    with patch.object(execution, "execute", side_effect=fail) as execute:
        result = worker.dispatch(config, binding, policy, maximum=2)
        assert result["state"] == "stopped" and result["processed"] == 1
        assert result["results"][0]["run_state"] == "failed"
        assert execute.call_count == 1
    with patch.object(execution, "execute", side_effect=RuntimeError("synthetic private text")):
        with pytest.raises(RuntimeError):
            worker.dispatch(config, binding, policy, maximum=1)
    view = status.synchronize(config, binding, runs[1])
    assert view["dispatch_phase"] == "unknown"
    assert "private" not in json.dumps(view)


@pytest.mark.parametrize("started", [False, True])
def test_cancel_sync_retains_calls_and_prevents_execution(config, binding, policy, started):
    bid, run_id = queued(config, binding, policy)
    db = database.connect(config.state_root)
    if started:
        ledger.start(config, db, run_id, policy)
        ledger.reserve(config, db, run_id, policy)
    before = main_rows(config, "model_calls")
    batches.choose(config, binding, bid, "cancel")
    view = status.synchronize(config, binding, run_id)
    assert view["run_state"] == "cancelled" and view["batch_state"] == "cancelled"
    assert main_rows(config, "model_calls") == before
    assert ledger.get_run(db, run_id)["error_code"] == "weixin_batch_cancelled"
    db.close()
    with patch.object(execution, "execute") as execute:
        assert worker.dispatch(config, binding, policy, maximum=1)["processed"] == 0
        execute.assert_not_called()


def test_cancel_between_tasks_preserves_success(config, binding, policy):
    bid, runs = two_articles(config, binding, policy)

    def cancel_after(*args):
        result = finish(*args)
        batches.choose(config, binding, bid, "cancel")
        return result

    with patch.object(execution, "execute", side_effect=cancel_after) as execute:
        result = worker.dispatch(config, binding, policy, maximum=2)
        assert result["state"] == "stopped" and execute.call_count == 1
    assert status.synchronize(config, binding, runs[0])["run_state"] == "succeeded"
    assert status.synchronize(config, binding, runs[1])["run_state"] == "cancelled"


def test_revoked_query_and_prior_calls_block_without_execute(config, binding, policy):
    _, run_id = queued(config, binding, policy)
    db = database.connect(config.state_root)
    auth.set_grant(db, binding.client_id, binding.vault_id, query=False, original=False, add=True)
    with patch.object(execution, "execute") as execute:
        result = worker.dispatch(config, binding, policy, maximum=1)
        assert result["blocked"]["error_code"] == "task_preflight_rejected"
        execute.assert_not_called()
    auth.set_grant(db, binding.client_id, binding.vault_id, query=True, original=False, add=True)
    ledger.start(config, db, run_id, policy)
    ledger.reserve(config, db, run_id, policy)
    ledger.transition(db, run_id, "queued")
    db.close()
    with patch.object(execution, "execute") as execute:
        result = worker.dispatch(config, binding, policy, maximum=1)
        assert result["blocked"]["error_code"] == "task_prior_model_effects"
        execute.assert_not_called()


@pytest.mark.parametrize(
    "cancel_during_call,schema_failure", [(False, False), (True, False), (False, True)]
)
def test_real_executor_with_local_protocol_and_synthetic_provider(
    config, binding, policy, cancel_during_call, schema_failure
):
    bid, run_id = queued(config, binding, policy)
    script = """
import json,sys
json.loads(sys.stdin.readline())
print(json.dumps({'type':'model','id':'1','body':{}}),flush=True)
reply=json.loads(sys.stdin.readline())
assert reply['ok']
print(json.dumps({'type':'event','event':{'type':'agent_end','messages':[{'role':'assistant','stopReason':'stop'}]}}),flush=True)
sys.stdin.read()
"""

    def provider_response(p, r, payload):
        if cancel_during_call:
            batches.choose(config, binding, bid, "cancel")
            status.synchronize(config, binding, run_id)
        body = response(payload)
        if schema_failure:
            body["choices"][0]["message"]["content"] = json.dumps(
                {"mode": "extractive", "quotations": [], "PRIVATE_FIELD": "PRIVATE"}
            )
        return body

    with (
        patch.object(execution, "command", return_value=[sys.executable, "-u", "-c", script]),
        patch.object(execution, "stop"),
        patch.object(model_gateway, "complete", side_effect=provider_response) as provider,
    ):
        result = worker.dispatch(config, binding, policy, maximum=1)
    assert result["results"][0]["run_state"] == (
        "cancelled" if cancel_during_call else "failed" if schema_failure else "succeeded"
    )
    if schema_failure:
        assert result["results"][0]["answer_rejection"] == {
            "code": "answer_output_rejected_schema",
            "schema_issues": [{"path": "$", "kind": "extra_forbidden", "input_type": "string"}],
        }
        assert "PRIVATE" not in json.dumps(result)
    assert provider.call_count == 1
    assert len(main_rows(config, "model_calls")) == 1
    db = database.connect(config.state_root)
    try:
        assert db.execute("SELECT state FROM model_calls").fetchone()[0] == "settled"
        if cancel_during_call:
            with pytest.raises(auth.Denied):
                answer_tasks.deliver(config, db, binding.client_id, run_id)
        elif schema_failure:
            assert answer_tasks.get(db, run_id)["result_json"] is None
            with pytest.raises(InvalidInput):
                answer_tasks.deliver(config, db, binding.client_id, run_id)
        else:
            assert answer_tasks.deliver(config, db, binding.client_id, run_id)
    finally:
        db.close()


def test_incomplete_submission_blocks_and_idempotent_submit_recovers(config, binding, policy):
    bid = setup(config, binding)
    with patch.object(ledger, "create_run", side_effect=RuntimeError("interrupted create")):
        with pytest.raises(RuntimeError):
            bindings.submit(config, binding, bid, policy)
    with patch.object(execution, "execute") as execute:
        result = worker.dispatch(config, binding, policy, maximum=1)
        assert result["blocked"]["error_code"] == "task_submission_incomplete"
        execute.assert_not_called()
    run_id = bindings.submit(config, binding, bid, policy)["run_id"]
    with patch.object(execution, "execute", side_effect=finish):
        result = worker.dispatch(config, binding, policy, maximum=1)
    assert result["results"][0]["run_id"] == run_id


def test_cancel_snapshot_failure_recovers_without_losing_main_state(config, binding, policy):
    bid, run_id = queued(config, binding, policy)
    batches.choose(config, binding, bid, "cancel")
    with patch.object(
        status, "initialize_locked", side_effect=RuntimeError("journal interruption")
    ):
        with pytest.raises(RuntimeError):
            status.synchronize(config, binding, run_id)
    assert main_rows(config, "execution_runs")[0]["state"] == "cancelled"
    assert status.synchronize(config, binding, run_id)["run_state"] == "cancelled"
    assert not main_rows(config, "model_calls")
