"""The API boundary enforces one durable allowance across multiple frame runs."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from fiber_bundle import (
    database,
    execution,
    execution_ledger,
    model_budgets,
    model_gateway,
    pi_processing,
    video_call_allowance,
)
from fiber_bundle.channels.intake_contract import Retry
from fiber_bundle.config import InvalidInput
from fiber_bundle.model_budgets import Exhausted

pytest_plugins = ["test_execution"]


def prepare_runs(config, policy, bound, allowance, count):
    with database.connect(config.state_root) as db:
        model_budgets.save(
            db,
            model_budgets.Save(
                secret_file=policy.secret_file,
                expected_revision=0,
                settings=model_budgets.Policy(label="Synthetic", limit=10000000),
            ),
        )
        version = execution_ledger.get_run(db, bound[0])["version_id"]
        result = []
        for number in range(count):
            rid = pi_processing.prepare(
                config,
                db,
                policy,
                bound[1],
                bound[2],
                version,
                "video",
                str(number) * 64,
                [{"id": str(number), "text": "Synthetic frame"}],
                allow_cloud=True,
            )
            video_call_allowance.bind(db, rid, {"call_allowance": allowance})
            execution_ledger.start(config, db, rid, policy)
            result.append(rid)
    return result


def test_multiple_runs_and_restart_cannot_exceed_resume_limit(config, policy, bound, monkeypatch):
    allowance = video_call_allowance.new(2)
    runs = prepare_runs(config, policy, bound, allowance, 4)
    calls = []
    monkeypatch.setattr(model_gateway, "prepare", lambda p, value: value)

    def provider(p, rid, request):
        calls.append(rid)
        return {
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    monkeypatch.setattr(model_gateway, "complete", provider)
    for rid in runs[:2]:
        execution.handle(config, policy, rid, {"type": "model", "id": "frame", "body": {}})
    for rid in runs[2:]:
        with pytest.raises(Exhausted, match="call_limit_reached"):
            execution.handle(config, policy, rid, {"type": "model", "id": "frame", "body": {}})
    with database.connect(config.state_root) as fresh:
        assert video_call_allowance.status(fresh, allowance) == {
            **allowance,
            "used": 2,
            "remaining": 0,
        }
        assert fresh.execute("select count(*) from model_calls").fetchone()[0] == 2
    assert len(calls) == 2


def test_simultaneous_reservations_share_atomic_limit(config, policy, bound):
    allowance = video_call_allowance.new(1)
    runs = prepare_runs(config, policy, bound, allowance, 4)

    def reserve(rid):
        with database.locked(config), database.connect(config.state_root) as db:
            try:
                execution_ledger.reserve(config, db, rid, policy)
                return True
            except Exhausted:
                return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(reserve, runs)) == 1


def test_uncertain_call_is_counted_and_no_provider_replay(config, policy, bound, monkeypatch):
    allowance = video_call_allowance.new(1)
    runs = prepare_runs(config, policy, bound, allowance, 2)
    monkeypatch.setattr(model_gateway, "prepare", lambda p, value: value)
    calls = []

    def uncertain(*args):
        calls.append(1)
        raise OSError("synthetic unknown response")

    monkeypatch.setattr(model_gateway, "complete", uncertain)
    with pytest.raises(OSError):
        execution.handle(config, policy, runs[0], {"type": "model", "id": "a", "body": {}})
    with pytest.raises(Exhausted):
        execution.handle(config, policy, runs[1], {"type": "model", "id": "b", "body": {}})
    with database.connect(config.state_root) as db:
        assert db.execute("select state from model_calls").fetchone()[0] == "uncertain"
    assert len(calls) == 1


def test_zero_allowance_blocks_before_network_and_reservation(config, policy, bound, monkeypatch):
    allowance = video_call_allowance.new(0)
    rid = prepare_runs(config, policy, bound, allowance, 1)[0]
    monkeypatch.setattr(model_gateway, "prepare", lambda p, value: value)
    monkeypatch.setattr(model_gateway, "complete", lambda *a: pytest.fail("network"))
    with pytest.raises(Exhausted):
        execution.handle(config, policy, rid, {"type": "model", "id": "a", "body": {}})
    with database.connect(config.state_root) as db:
        assert db.execute("select count(*) from model_calls").fetchone()[0] == 0


@pytest.mark.parametrize("value", [True, -1, 1.5, "10", 1001])
def test_retry_rejects_invalid_allowance(value):
    with pytest.raises(ValidationError):
        Retry(expected_revision=1, max_new_model_calls=value)


def test_allowance_does_not_change_historical_run_binding(config, policy, bound):
    allowance = video_call_allowance.new(1)
    rid = prepare_runs(config, policy, bound, allowance, 1)[0]
    with database.connect(config.state_root) as db:
        execution_ledger.reserve(config, db, rid, policy)
        old = db.execute(
            "select input_json from pi_processing_runs where run_id=?", (rid,)
        ).fetchone()[0]
        with pytest.raises(InvalidInput, match="call_allowance_invalid"):
            video_call_allowance.bind(db, rid, {"call_allowance": video_call_allowance.new(20)})
        assert (
            db.execute(
                "select input_json from pi_processing_runs where run_id=?", (rid,)
            ).fetchone()[0]
            == old
        )
        assert json.loads(old)["video_call_allowance"] == allowance
