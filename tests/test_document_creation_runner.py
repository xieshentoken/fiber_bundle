"""Opt-in actual Docker/Pi tests. All replies are synthetic and all projects isolated."""

import os
import threading
from dataclasses import replace
from unittest.mock import patch

import pytest
from test_document_creation_config import creation_policy as creation_policy
from test_document_creation_execution import prepared_task as prepared_task
from test_document_creation_tasks import cancel
from test_document_creation_tasks import input_task as input_task
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner

from fiber_bundle import database, execution_ledger
from fiber_bundle import document_creation_artifacts as artifacts
from fiber_bundle import document_creation_execution as execution
from fiber_bundle import document_creation_profile as profile
from fiber_bundle import document_creation_runner as runner
from fiber_bundle import document_creation_tasks as tasks
from fiber_bundle.config import InvalidInput
from fiber_bundle.document_creation_config import parse

pytestmark = pytest.mark.skipif(
    os.environ.get("FIBER_M11_DOCKER") != "1", reason="explicit synthetic Docker opt-in required"
)


@pytest.fixture
def policy(creation_policy):
    creation_policy["model_config"].update(
        image=profile.IMAGE,
        max_calls=2,
        daily_calls=8,
        max_input_tokens=131072,
        max_request_bytes=131072,
        task_limit_nusd=100000000,
        daily_limit_nusd=200000000,
        timeout_seconds=20,
    )
    creation_policy["capability_revision"] = profile.REVISION
    return parse(creation_policy)


def run(config, owner, task, policy):
    with (
        patch("fiber_bundle.model_gateway.complete", side_effect=AssertionError("provider")),
        patch("fiber_bundle.execution_config.read_secret", side_effect=AssertionError("secret")),
    ):
        return runner.run_synthetic(
            config,
            owner[0],
            task["task_id"],
            policy,
            "# Synthetic draft\n100 psi",
            expected_revision=task["revision"],
            expected_plan_sha256=task["plan_sha256"],
            expected_configuration_sha256=policy.sha256,
            allow_synthetic=True,
        )


@pytest.mark.parametrize("fmt", ["md", "txt"])
def test_actual_fixed_pi_completes_two_billed_rounds_and_persists_private_local_draft(
    config, owner, prepared_task, policy, fmt
):
    if fmt == "txt":
        policy = replace(policy, formats=("md", "txt"))
        db = database.connect(config.state_root)
        plan = tasks.plan(config, db, owner[0], prepared_task["task_id"])
        prepared_task = tasks.submit(
            config, db, owner[0], "txt-draft", plan.query_task_id, "Prepare text", "txt"
        )
        db.close()
    report = run(config, owner, prepared_task, policy)
    assert report["state"] == "succeeded"
    assert report["runtime"] == {
        "mode": "synthetic",
        "profile_sha256": report["runtime"]["profile_sha256"],
        "round_count": 2,
        "real_provider_calls": 0,
    }
    assert len(report["model_calls"]) == 2 and all(
        c["state"] == "settled" for c in report["model_calls"]
    )
    assert (
        report["delivery_allowed"] is report["save_allowed"] is report["runtime_verified"] is False
    )
    assert report["draft_version"]["artifact"]["original_name"] == "draft." + fmt
    with database.locked(config):
        db = database.connect(config.state_root)
        raw, metadata = artifacts.get(config, db, owner[0], prepared_task["task_id"])
        assert raw.endswith(b"100 psi") and metadata == report["draft_version"]
        assert tasks.status(config, db, owner[0], prepared_task["task_id"])["execution"] == report
        before = list(db.iterdump())
        db.close()
    with pytest.raises(InvalidInput, match="not_replayable"):
        run(config, owner, prepared_task, policy)
    db = database.connect(config.state_root)
    assert list(db.iterdump()) == before
    assert not db.execute("PRAGMA foreign_key_check").fetchall()
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    db.close()


def test_actual_pi_second_call_budget_denied_keeps_first_fee_and_worker_file(
    config, owner, prepared_task, policy
):
    policy = replace(policy, model_config=replace(policy.policy(), max_calls=1))
    with pytest.raises(execution_ledger.BudgetExceeded):
        run(config, owner, prepared_task, policy)
    db = database.connect(config.state_root)
    report = execution.status(db, owner[0], prepared_task["task_id"])
    assert report["state"] == "failed" and report["error_code"] == "document_budget_exhausted"
    assert len(report["model_calls"]) == 1 and report["model_calls"][0]["state"] == "settled"
    assert report["runtime"]["round_count"] == 1
    assert (config.state_root / "document-runs" / report["run_id"] / "draft.md").exists()
    assert not db.execute("SELECT * FROM document_creation_artifacts").fetchall()
    db.close()


def test_actual_pi_full_first_request_checked_before_synthetic_dispatch(
    config, owner, prepared_task, policy
):
    policy = replace(policy, model_config=replace(policy.policy(), max_request_bytes=1024))
    with pytest.raises(InvalidInput, match="request too large"):
        run(config, owner, prepared_task, policy)
    db = database.connect(config.state_root)
    report = execution.status(db, owner[0], prepared_task["task_id"])
    assert report["state"] == "failed" and report["runtime"]["round_count"] == 0
    assert report["model_calls"][0]["state"] == "uncertain"
    db.close()


def test_actual_pi_cancel_after_first_reply_stops_without_second_reservation(
    config, owner, prepared_task, policy, monkeypatch
):
    ready, done = threading.Event(), threading.Event()
    errors = []
    original = runner._send

    def held(process, frame, deadline, check):
        original(process, frame, deadline, check)
        if frame["type"] == "reply" and frame["id"] == "1":
            ready.set()
            assert done.wait(5)

    monkeypatch.setattr(runner, "_send", held)

    def cancelling():
        try:
            assert ready.wait(10)
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    cancel(config, db, owner[0], prepared_task)
                finally:
                    db.close()
        except Exception as exc:
            errors.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=cancelling)
    thread.start()
    try:
        with pytest.raises(InvalidInput, match="not_active"):
            run(config, owner, prepared_task, policy)
    finally:
        thread.join(timeout=10)
    assert not thread.is_alive() and not errors
    db = database.connect(config.state_root)
    result = tasks.status(config, db, owner[0], prepared_task["task_id"])
    assert result["state"] == result["execution"]["state"] == "cancelled"
    assert (
        len(result["execution"]["model_calls"]) == 1
        and result["execution"]["model_calls"][0]["state"] == "settled"
    )
    assert not db.execute("SELECT * FROM document_creation_artifacts").fetchall()
    db.close()


def test_actual_pi_timeout_stops_container_and_does_not_replay_known_reply(
    config, owner, prepared_task, policy, monkeypatch
):
    policy = replace(policy, model_config=replace(policy.policy(), timeout_seconds=3))
    original = runner._send

    def not_delivered(process, frame, deadline, check):
        if frame["type"] == "init":
            original(process, frame, deadline, check)

    monkeypatch.setattr(runner, "_send", not_delivered)
    with pytest.raises(TimeoutError):
        run(config, owner, prepared_task, policy)
    db = database.connect(config.state_root)
    report = execution.status(db, owner[0], prepared_task["task_id"])
    assert report["state"] == "failed" and report["error_code"] == "document_runtime_timeout"
    assert report["runtime"]["round_count"] == 1
    assert len(report["model_calls"]) == 1 and report["model_calls"][0]["state"] == "settled"
    db.close()
    result = runner.recover_synthetic(config, owner[0], prepared_task["task_id"])
    assert result == report
    with pytest.raises(InvalidInput, match="not_replayable"):
        run(config, owner, prepared_task, policy)


def test_recovery_removes_a_released_tasks_actual_orphan_container_without_replay(
    config, owner, prepared_task, policy, tmp_path
):
    import subprocess

    from test_document_creation_execution import claim

    from fiber_bundle import document_creation_rounds as rounds
    from fiber_bundle import executor_container

    with execution.responsibility(config, owner[0], prepared_task["task_id"]) as guard:
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                attempt = claim(config, db, owner[0], prepared_task, policy, guard)
                rounds.attach(config, db, attempt, policy, guard)
            finally:
                db.close()
        command = executor_container.command(
            policy.policy().image, tmp_path, attempt.run_id, network="none"
        )
        command.insert(2, "--detach")
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=20)
            inspected = subprocess.run(
                ["docker", "inspect", executor_container.container_name(attempt.run_id)],
                check=True,
                capture_output=True,
                timeout=10,
            )
            assert b'"Running": true' in inspected.stdout
            with pytest.raises(InvalidInput, match="already owned"):
                runner.recover_synthetic(config, owner[0], prepared_task["task_id"])
        except BaseException:
            executor_container.stop(attempt.run_id)
            raise
    try:
        result = runner.recover_synthetic(config, owner[0], prepared_task["task_id"])
        assert result["state"] == "interrupted"
        assert result["model_calls"][0]["state"] == "uncertain"
        assert result["runtime"]["round_count"] == 0
        assert runner.recover_synthetic(config, owner[0], prepared_task["task_id"]) == result
        with pytest.raises(InvalidInput, match="not_replayable"):
            run(config, owner, prepared_task, policy)
    finally:
        runner._stop(attempt.run_id)


def test_actual_pi_provider_candidate_drains_frozen_revision_using_mock_gateway_only(
    config, owner, prepared_task, policy, monkeypatch
):
    import json

    from fiber_bundle import document_creation_continuation as continuation
    from fiber_bundle import document_creation_worker as worker

    policy = replace(policy, model_config=replace(policy.policy(), max_calls=4))
    calls, queued = [], []

    def complete(model, run_id, request, *, require_message):
        assert require_message is False
        calls.append((run_id, request))
        ordinal = sum(v[0] == run_id for v in calls)
        if len(calls) == 1:
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    current = tasks.status(config, db, owner[0], prepared_task["task_id"])
                    queued.append(
                        continuation.enqueue(
                            config,
                            db,
                            owner[0],
                            prepared_task["task_id"],
                            "add-during-model",
                            "Add a conclusion",
                            expected_revision=current["revision"],
                            expected_sha256=current["plan_sha256"],
                        )
                    )
                finally:
                    db.close()
        return runner._reply(
            policy, ordinal, "# Mock provider version " + str(len(queued) if len(calls) <= 2 else 2)
        )

    monkeypatch.setattr("fiber_bundle.model_gateway.complete", complete)
    monkeypatch.setattr(
        "fiber_bundle.execution_config.read_secret", lambda *args: pytest.fail("secret read")
    )
    results = worker.drain(
        config, owner[0], prepared_task["task_id"], policy, max_tasks=2, allow_provider=True
    )
    assert len(results) == 2 and len(calls) == 4
    assert all(v["state"] == "succeeded" and v["runtime"]["mode"] == "provider" for v in results)
    assert all(
        v["runtime_verified"] is False and v["runtime"]["real_provider_calls"] == 2 for v in results
    )
    assert all(len(v["model_calls"]) == 2 for v in results)
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            first, meta = artifacts.get(config, db, owner[0], prepared_task["task_id"])
            child, next_meta = artifacts.get(config, db, owner[0], queued[0]["child"]["task_id"])
            assert first.endswith(b"version 1") and child.endswith(b"version 2")
            assert meta["artifact"]["sha256"] != next_meta["artifact"]["sha256"]
            text = calls[2][1]["messages"][1]["content"]
            prompt = json.loads(text[text.index("{") :])
            assert prompt["previous_unverified_draft"].encode() == first
            assert (
                db.execute("SELECT count(*) FROM model_calls WHERE state='settled'").fetchone()[0]
                == 4
            )
            assert not db.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            db.close()
    # Historical queue inspection never invokes a model again.
    assert (
        len(
            worker.drain(
                config, owner[0], prepared_task["task_id"], policy, max_tasks=2, allow_provider=True
            )
        )
        == 2
    )
    assert len(calls) == 4


def test_actual_pi_provider_late_reply_after_cancel_keeps_known_cost_no_second_call(
    config, owner, prepared_task, policy, monkeypatch
):
    calls = []

    def complete(model, run_id, request, **kwargs):
        calls.append(run_id)
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                cancel(config, db, owner[0], prepared_task)
            finally:
                db.close()
        return runner._reply(policy, 1, "late mock draft")

    monkeypatch.setattr("fiber_bundle.model_gateway.complete", complete)
    with pytest.raises(InvalidInput, match="not_active"):
        runner.run_provider(
            config,
            owner[0],
            prepared_task["task_id"],
            policy,
            expected_revision=prepared_task["revision"],
            expected_plan_sha256=prepared_task["plan_sha256"],
            expected_configuration_sha256=policy.sha256,
            allow_provider=True,
        )
    db = database.connect(config.state_root)
    report = execution.status(db, owner[0], prepared_task["task_id"])
    assert len(calls) == 1 and len(report["model_calls"]) == 1
    assert report["model_calls"][0]["state"] == "settled"
    assert report["model_calls"][0]["charged_nusd"] > 0
    assert not db.execute("SELECT * FROM document_creation_artifacts").fetchall()
    db.close()
