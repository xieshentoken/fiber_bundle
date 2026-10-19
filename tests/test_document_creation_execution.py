import json
import os
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from test_document_creation_config import creation_policy as creation_policy
from test_document_creation_tasks import amend, cancel, submit
from test_document_creation_tasks import input_task as input_task
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner

import fiber_bundle
from fiber_bundle import auth, database, execution_ledger
from fiber_bundle import document_creation_execution as creation
from fiber_bundle import document_creation_ledger as sidecar
from fiber_bundle import document_creation_tasks as tasks
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.document_creation_config import parse
from fiber_bundle.inputs import canonical


@pytest.fixture
def prepared_task(config, owner, input_task):
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            return submit(config, db, owner[0], input_task[0])
        finally:
            db.close()


def claim(config, db, cid, task, policy, guard, **overrides):
    options = {
        "expected_revision": task["revision"],
        "expected_plan_sha256": task["plan_sha256"],
        "expected_configuration_sha256": policy.sha256,
        "allow_reservation": True,
        **overrides,
    }
    return creation.claim(config, db, cid, task["task_id"], policy, guard, **options)


def counts(db):
    return {
        t: db.execute("SELECT count(*) FROM " + t).fetchone()[0]
        for t in ("execution_runs", "document_creation_runs", "model_calls")
    }


@pytest.fixture
def active(config, owner, prepared_task, creation_policy):
    policy = parse(creation_policy)
    with creation.responsibility(config, owner[0], prepared_task["task_id"]) as guard:
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                attempt = claim(config, db, owner[0], prepared_task, policy, guard)
                yield db, policy, guard, attempt
            finally:
                db.close()


def test_claim_has_explicit_default_off_gate(config, owner, prepared_task, creation_policy):
    policy = parse(creation_policy)
    with creation.responsibility(config, owner[0], prepared_task["task_id"]) as guard:
        with database.locked(config):
            db = database.connect(config.state_root)
            before = list(db.iterdump())
            with pytest.raises(InvalidInput, match="reservation_not_enabled"):
                claim(config, db, owner[0], prepared_task, policy, guard, allow_reservation=False)
            assert list(db.iterdump()) == before
            db.close()


def test_claim_persists_one_owner_binding_and_reserved_fee_without_inference(
    config, owner, prepared_task, creation_policy
):
    policy = parse(creation_policy)
    wire = (config.state_root / "document-creation.sqlite3").read_bytes()
    with (
        patch("fiber_bundle.execution_config.read_secret", side_effect=AssertionError("secret")),
        patch("fiber_bundle.model_gateway.complete", side_effect=AssertionError("model")),
        patch("socket.create_connection", side_effect=AssertionError("network")),
        patch("subprocess.Popen", side_effect=AssertionError("process")),
        creation.responsibility(config, owner[0], prepared_task["task_id"]) as guard,
    ):
        with database.locked(config):
            db = database.connect(config.state_root)
            attempt = claim(config, db, owner[0], prepared_task, policy, guard)
            payload = creation.current(config, db, attempt, policy, guard)
            report = creation.status(db, owner[0], prepared_task["task_id"])
            assert counts(db) == {
                "execution_runs": 1,
                "document_creation_runs": 1,
                "model_calls": 1,
            }
            assert report["state"] == "running" and report["model_call"]["state"] == "reserved"
            assert report["model_call"]["reserved_nusd"] == policy.policy().reserve_nusd
            from fiber_bundle.usage_credentials import reference

            credential = db.execute(
                "SELECT credential_id FROM model_call_credentials WHERE call_id=?",
                (attempt.call_id,),
            ).fetchone()
            assert credential["credential_id"] == reference(policy.policy().secret_file)["id"]
            assert report["request_limits_checked"] is True
            assert (
                report["execution_allowed"]
                is report["delivery_allowed"]
                is report["save_allowed"]
                is False
            )
            assert payload["model"] == policy.policy().model and "tools" not in payload
            assert "100 psi session evidence" in canonical(payload)
            assert "RAW-EVENT-SECRET" not in canonical(payload)
            assert owner[0] not in canonical(payload) and prepared_task["task_id"] not in canonical(
                payload
            )
            assert "制作汇报" not in canonical(report) and "100 psi" not in canonical(report)
            assert guard.owner_id not in canonical(
                report
            ) and policy.policy().secret_file not in canonical(report)
            assert (
                tasks.status(config, db, owner[0], prepared_task["task_id"])["execution"] == report
            )
            assert not db.execute("PRAGMA foreign_key_check").fetchall()
            with pytest.raises(InvalidInput, match="not_replayable"):
                claim(config, db, owner[0], prepared_task, policy, guard)
            assert (config.state_root / "document-creation.sqlite3").read_bytes() == wire
            db.close()
    with database.locked(config):
        db = database.connect(config.state_root)
        assert creation.status(db, owner[0], prepared_task["task_id"])["state"] == "interrupted"
        assert (
            creation.status(db, owner[0], prepared_task["task_id"])["model_call"]["state"]
            == "uncertain"
        )
        db.close()


def test_active_owner_blocks_second_owner_and_recovery(config, owner, prepared_task, active):
    with pytest.raises(InvalidInput, match="already owned"):
        with creation.responsibility(config, owner[0], prepared_task["task_id"]):
            pass
    with pytest.raises(InvalidInput, match="already owned"):
        creation.recover(config, owner[0], prepared_task["task_id"])


@pytest.mark.parametrize("mutation", ["owner", "call", "run", "config", "task"])
def test_forged_attempt_cannot_access_bound_request_or_accounting(config, active, mutation):
    db, policy, guard, attempt = active
    changed = replace(
        attempt,
        **{
            "owner": {"owner_id": "another"},
            "call": {"call_id": "another"},
            "run": {"run_id": "another"},
            "config": {"configuration_sha256": "0" * 64},
            "task": {"task_id": "another"},
        }[mutation],
    )
    before = list(db.iterdump())
    with pytest.raises(InvalidInput):
        creation.current(config, db, changed, policy, guard)
    with pytest.raises(InvalidInput):
        creation.record_usage(db, changed, {"prompt_tokens": 1, "completion_tokens": 1})
    assert list(db.iterdump()) == before


@pytest.mark.parametrize("mutation", ["cancel", "grant", "source", "client", "policy"])
def test_authority_and_configuration_rechecked_before_request(config, owner, active, mutation):
    db, policy, guard, attempt = active
    if mutation == "cancel":
        task = tasks.status(config, db, owner[0], attempt.task_id)
        cancel(config, db, owner[0], task)
    elif mutation == "grant":
        auth.set_grant(db, owner[0], owner[2]["personal"], query=False, original=False, add=True)
    elif mutation == "source":
        (config.project_root / "test vault/temp_vault1/new.md").write_text(
            "changed pressure 90 psi"
        )
    elif mutation == "client":
        auth.revoke_client(db, owner[0])
    else:
        policy = replace(policy, capability_revision="new-capability")
    with pytest.raises((auth.Denied, InvalidInput, IntegrityError)):
        creation.current(config, db, attempt, policy, guard)
    result = creation.record_usage(db, attempt, {"prompt_tokens": 10, "completion_tokens": 20})
    assert result["model_call"]["charged_nusd"] == 10 * 150 + 20 * 500
    assert result["execution_allowed"] is False
    if mutation == "cancel":
        assert result["state"] == "cancelled"


def test_old_generic_executor_cannot_start_or_charge_document_run(config, active):
    db, policy, guard, attempt = active
    before = list(db.iterdump())
    with pytest.raises(auth.Denied):
        execution_ledger.authorize(config, db, execution_ledger.get_run(db, attempt.run_id))
    with pytest.raises(auth.Denied):
        execution_ledger.start(config, db, attempt.run_id, policy.policy())
    with pytest.raises(auth.Denied):
        execution_ledger.reserve(config, db, attempt.run_id, policy.policy())
    assert list(db.iterdump()) == before


def test_cancel_preserves_reservation_and_known_late_usage(config, owner, active):
    db, policy, guard, attempt = active
    task = tasks.status(config, db, owner[0], attempt.task_id)
    cancelled = cancel(config, db, owner[0], task)
    assert cancelled["state"] == cancelled["execution"]["state"] == "cancelled"
    assert cancelled["execution"]["model_call"]["state"] == "uncertain"
    assert cancelled["execution"]["model_call"]["reserved_nusd"] == policy.policy().reserve_nusd
    before = list(db.iterdump())
    assert cancel(config, db, owner[0], cancelled) == cancelled
    assert list(db.iterdump()) == before
    result = creation.record_usage(db, attempt, {"prompt_tokens": 10, "completion_tokens": 20})
    assert result["state"] == "cancelled" and result["model_call"]["state"] == "settled"


def test_bound_task_cannot_accept_unscheduled_requirements(config, owner, active):
    db, _, _, attempt = active
    task = tasks.status(config, db, owner[0], attempt.task_id)
    with pytest.raises(InvalidInput, match="execution_bound"):
        amend(config, db, owner[0], task)


def test_cancel_stop_survives_separate_sidecar_audit_failure(config, owner, active, monkeypatch):
    db, _, _, attempt = active
    task = tasks.status(config, db, owner[0], attempt.task_id)
    wire = (config.state_root / "document-creation.sqlite3").read_bytes()
    monkeypatch.setattr(
        sidecar,
        "event",
        lambda *args: (_ for _ in ()).throw(
            sqlite3.OperationalError("synthetic sidecar audit fault")
        ),
    )
    with pytest.raises(sqlite3.OperationalError):
        cancel(config, db, owner[0], task)
    assert (config.state_root / "document-creation.sqlite3").read_bytes() == wire
    report = tasks.status(config, db, owner[0], attempt.task_id)
    assert report["state"] == "prepared" and report["execution"]["state"] == "cancelled"
    assert report["execution"]["model_call"]["state"] == "uncertain"


@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": True, "completion_tokens": 1}])
def test_invalid_usage_stays_uncertain_and_non_replayable(config, active, usage):
    db, _, _, attempt = active
    result = creation.record_usage(db, attempt, usage)
    assert result["state"] == "failed" and result["error_code"] == "document_usage_rejected"
    assert result["model_call"]["state"] == "uncertain"


def test_usage_overrun_is_charged_before_failure_and_not_refunded(config, active):
    db, policy, _, attempt = active
    incoming, outgoing = policy.policy().max_input_tokens + 1, 2
    result = creation.record_usage(
        db, attempt, {"prompt_tokens": incoming, "completion_tokens": outgoing}
    )
    assert result["state"] == "failed" and result["model_call"]["state"] == "settled"
    assert result["model_call"]["charged_nusd"] == incoming * 150 + outgoing * 500


def test_repeated_known_usage_is_idempotent_but_conflicting_usage_rejected(config, active):
    db, policy, guard, attempt = active
    usage = {"prompt_tokens": 10, "completion_tokens": 20}
    first = creation.record_usage(db, attempt, usage)
    before = list(db.iterdump())
    assert creation.record_usage(db, attempt, usage) == first
    with pytest.raises(InvalidInput, match="usage_conflict"):
        creation.record_usage(db, attempt, {"prompt_tokens": 11, "completion_tokens": 20})
    assert list(db.iterdump()) == before
    with pytest.raises(InvalidInput, match="already_accounted"):
        creation.current(config, db, attempt, policy, guard)


def test_new_owner_cannot_reclaim_interrupted_task(config, owner, prepared_task, creation_policy):
    policy = parse(creation_policy)
    for index in range(2):
        with creation.responsibility(config, owner[0], prepared_task["task_id"]) as guard:
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    if index == 0:
                        claim(config, db, owner[0], prepared_task, policy, guard)
                    else:
                        with pytest.raises(InvalidInput, match="not_replayable"):
                            claim(config, db, owner[0], prepared_task, policy, guard)
                        assert list(counts(db).values()) == [1, 1, 1]
                finally:
                    db.close()


@pytest.mark.parametrize("mutation", ["head", "configuration", "cancel", "owner"])
def test_bad_claim_binding_never_creates_owner_or_cost(
    config, owner, prepared_task, creation_policy, mutation
):
    policy = parse(creation_policy)
    with creation.responsibility(config, owner[0], prepared_task["task_id"]) as guard:
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                changes = {}
                if mutation == "head":
                    changes["expected_plan_sha256"] = "0" * 64
                elif mutation == "configuration":
                    changes["expected_configuration_sha256"] = "0" * 64
                elif mutation == "cancel":
                    cancel(config, db, owner[0], prepared_task)
                else:
                    guard = creation.Responsibility("another", prepared_task["task_id"])
                before = list(db.iterdump())
                with pytest.raises(InvalidInput):
                    claim(config, db, owner[0], prepared_task, policy, guard, **changes)
                assert list(db.iterdump()) == before and not any(counts(db).values())
            finally:
                db.close()


def test_bound_status_and_cancel_preserve_owner_isolation(config, owner, active):
    db, _, _, attempt = active
    other = auth.create_client(db, "other", False)["id"]
    before = list(db.iterdump())
    for action in (creation.status, creation.cancel):
        with pytest.raises(auth.Denied):
            action(db, other, attempt.task_id)
    assert list(db.iterdump()) == before


@pytest.mark.parametrize("limit", ["bytes", "tokens"])
def test_actual_first_request_limits_reject_before_any_run_or_fee(
    config, owner, prepared_task, creation_policy, limit
):
    creation_policy["model_config"].update(
        {"max_request_bytes": 32} if limit == "bytes" else {"max_input_tokens": 32}
    )
    policy = parse(creation_policy)
    with creation.responsibility(config, owner[0], prepared_task["task_id"]) as guard:
        with database.locked(config):
            db = database.connect(config.state_root)
            with pytest.raises(InvalidInput):
                claim(config, db, owner[0], prepared_task, policy, guard)
            assert not any(counts(db).values())
            db.close()


@pytest.mark.parametrize(
    "prefix",
    [
        "INSERT INTO execution_runs",
        "INSERT INTO document_creation_runs",
        "INSERT INTO model_calls",
        "COMMIT",
    ],
)
def test_claim_write_fault_rolls_back_owner_binding_and_reservation(
    config, owner, prepared_task, creation_policy, prefix
):
    class Fault(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith(prefix) and prefix == "COMMIT":
                raise sqlite3.OperationalError("synthetic claim commit fault")
            result = super().execute(sql, parameters)
            if sql.startswith(prefix):
                raise sqlite3.OperationalError("synthetic claim write fault")
            return result

    with creation.responsibility(config, owner[0], prepared_task["task_id"]) as guard:
        with database.locked(config):
            db = sqlite3.connect(
                config.state_root / "metadata.sqlite3", isolation_level=None, factory=Fault
            )
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            before = list(db.iterdump())
            with pytest.raises(sqlite3.OperationalError):
                claim(config, db, owner[0], prepared_task, parse(creation_policy), guard)
            assert list(db.iterdump()) == before
            db.close()


def test_independent_connections_only_one_attempt_per_task(
    config, owner, prepared_task, creation_policy
):
    barrier = threading.Barrier(2)

    def reserve(_):
        barrier.wait(timeout=10)
        try:
            with creation.responsibility(config, owner[0], prepared_task["task_id"]) as guard:
                with database.locked(config):
                    db = database.connect(config.state_root)
                    try:
                        claim(config, db, owner[0], prepared_task, parse(creation_policy), guard)
                        return True
                    finally:
                        db.close()
        except InvalidInput:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(reserve, range(2))) == 1
    with database.locked(config):
        db = database.connect(config.state_root)
        assert list(counts(db).values()) == [1, 1, 1]
        db.close()


def test_distinct_tasks_compete_for_same_atomic_daily_allowance(
    config, owner, input_task, prepared_task, creation_policy
):
    with database.locked(config):
        db = database.connect(config.state_root)
        second = submit(config, db, owner[0], input_task[0], name="second")
        db.close()
    creation_policy["model_config"]["daily_calls"] = 1
    barrier = threading.Barrier(2)

    def reserve(task):
        barrier.wait(timeout=10)
        try:
            with creation.responsibility(config, owner[0], task["task_id"]) as guard:
                with database.locked(config):
                    db = database.connect(config.state_root)
                    try:
                        claim(config, db, owner[0], task, parse(creation_policy), guard)
                        return True
                    finally:
                        db.close()
        except execution_ledger.BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(reserve, [prepared_task, second])) == 1
    with database.locked(config):
        db = database.connect(config.state_root)
        assert list(counts(db).values()) == [1, 1, 1]
        db.close()


def test_process_exit_then_recovery_preserves_unknown_fee_and_refuses_replay(
    config, owner, prepared_task, creation_policy, tmp_path
):
    policy_file = tmp_path / "synthetic-policy.json"
    policy_file.write_text(json.dumps(creation_policy))
    code = """
import json,os,sys
from pathlib import Path
from dataclasses import asdict
from fiber_bundle import database,document_creation_execution as creation
from fiber_bundle.config import load_config
from fiber_bundle.document_creation_config import load
root=Path(sys.argv[1]);config=load_config(root/'config.json',root)
task=json.loads(sys.argv[3]);policy=load(Path(sys.argv[4]))
with creation.responsibility(config,sys.argv[2],task['task_id']) as guard:
 with database.locked(config):
  db=database.connect(config.state_root)
  attempt=creation.claim(config,db,sys.argv[2],task['task_id'],policy,guard,
   expected_revision=task['revision'],expected_plan_sha256=task['plan_sha256'],
   expected_configuration_sha256=policy.sha256,allow_reservation=True)
  print(json.dumps(asdict(attempt)),flush=True)
  os._exit(42)
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(fiber_bundle.__file__).parent.parent)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(config.project_root),
            owner[0],
            json.dumps(prepared_task),
            str(policy_file),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 42, result.stderr
    attempt = creation.Attempt(**json.loads(result.stdout))
    before = creation.recover(config, owner[0], prepared_task["task_id"])
    assert before["state"] == "interrupted" and before["model_call"]["state"] == "uncertain"
    assert creation.recover(config, owner[0], prepared_task["task_id"]) == before
    with creation.responsibility(config, owner[0], prepared_task["task_id"]) as guard:
        with database.locked(config):
            db = database.connect(config.state_root)
            with pytest.raises(InvalidInput, match="not_replayable"):
                claim(config, db, owner[0], prepared_task, parse(creation_policy), guard)
            usage = creation.record_usage(db, attempt, {"prompt_tokens": 1, "completion_tokens": 2})
            assert usage["state"] == "interrupted" and usage["model_call"]["charged_nusd"] == 1150
            assert list(counts(db).values()) == [1, 1, 1]
            db.close()
