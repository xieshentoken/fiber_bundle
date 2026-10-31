"""Per-video input/output accounting, history, atomic reservations and API limits."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from pydantic import ValidationError
from test_video_call_allowance import prepare_runs

from fiber_bundle import database, execution_ledger, model_budgets, video_call_allowance
from fiber_bundle import video_token_budget as budget
from fiber_bundle.channels.intake_contract import Retry
from fiber_bundle.config import InvalidInput
from fiber_bundle.model_budgets import Exhausted
from fiber_bundle.video_skill_settings import Settings

pytest_plugins = ["test_execution"]


def prepared(config, policy, bound, count=4, limit=500_000):
    selected = replace(policy, max_input_tokens=100, max_output_tokens=100)
    runs = prepare_runs(config, selected, bound, video_call_allowance.new(1000), count)
    frozen = {"job_id": "synthetic-video", "token_budget": {"limit": limit}}
    budget.configure(frozen)
    with database.connect(config.state_root) as db:
        for rid in runs:
            budget.bind(db, rid, frozen, 200)
    return selected, runs, frozen


def test_actual_input_output_releases_unused_reservation_across_frames(config, policy, bound):
    selected, runs, frozen = prepared(config, policy, bound, limit=250)
    with database.connect(config.state_root) as db:
        first = execution_ledger.reserve(config, db, runs[0], selected)
        assert budget.status(db, budget.descriptor(frozen))["remaining_tokens"] == 50
        with pytest.raises(Exhausted, match="video_skill_token_budget_exhausted"):
            execution_ledger.reserve(config, db, runs[1], selected)
        execution_ledger.settle(db, first, selected, {"prompt_tokens": 10, "completion_tokens": 20})
        execution_ledger.reserve(config, db, runs[1], selected)
    with database.connect(config.state_root) as reopened:
        state = budget.status(reopened, budget.descriptor(frozen))
        assert (state["actual_tokens"], state["reserved_tokens"], state["remaining_tokens"]) == (
            30,
            200,
            20,
        )
        assert (state["input_tokens"], state["output_tokens"]) == (10, 20)
        assert reopened.execute("select count(*) from model_calls").fetchone()[0] == 2


def test_concurrent_frames_cannot_overspend_even_with_money_key_budget(config, policy, bound):
    selected, runs, frozen = prepared(config, policy, bound, limit=300)
    with database.connect(config.state_root) as db:
        identity = model_budgets.key(db, selected.secret_file)
        row = model_budgets.get(db, identity)
        settings = dict(row["settings"], unit="USD_nano", limit=10**12)
        db.execute(
            "update model_budget_policies set value=? where credential_id=?",
            (json.dumps(settings), identity),
        )

    def reserve(rid):
        with database.locked(config), database.connect(config.state_root) as db:
            try:
                execution_ledger.reserve(config, db, rid, selected)
                return True
            except Exhausted:
                return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(reserve, runs)) == 1
    with database.connect(config.state_root) as db:
        state = budget.status(db, budget.descriptor(frozen))
        assert state["reserved_tokens"] == 200 and state["remaining_tokens"] == 100


def test_failed_unknown_usage_stays_reserved_and_token_limit_changes_do_not_reset(
    config, policy, bound
):
    selected, runs, frozen = prepared(config, policy, bound, limit=300)
    with database.connect(config.state_root) as db:
        execution_ledger.reserve(config, db, runs[0], selected)
        execution_ledger.interrupt(db, runs[0])
        assert budget.status(db, budget.descriptor(frozen))["used_tokens"] == 200
        budget.configure(frozen, 350)
        budget.bind(db, runs[1], frozen, 200)
        with pytest.raises(Exhausted):
            execution_ledger.reserve(config, db, runs[1], selected)
        budget.configure(frozen, 400)
        budget.bind(db, runs[1], frozen, 200)
        execution_ledger.reserve(config, db, runs[1], selected)
        assert budget.status(db, budget.descriptor(frozen))["used_tokens"] == 400


def test_legacy_success_and_failed_runs_count_once_without_mutating_history(config, policy, bound):
    selected = replace(policy, max_input_tokens=100, max_output_tokens=100)
    runs = prepare_runs(config, selected, bound, video_call_allowance.new(10), 2)
    with database.connect(config.state_root) as db:
        first = execution_ledger.reserve(config, db, runs[0], selected)
        execution_ledger.settle(db, first, selected, {"prompt_tokens": 20, "completion_tokens": 30})
        execution_ledger.reserve(config, db, runs[1], selected)
        before = [tuple(r) for r in db.execute("select * from pi_processing_runs order by run_id")]
        frozen = {
            "job_id": "legacy-job",
            "vision_runs": {"F1": runs[0]},
            "previous_vision_runs": [{"run_id": runs[1]}, {"run_id": runs[0]}],
            "call_allowance": video_call_allowance.new(0),
        }
        budget.configure(frozen, 500_000)
        state = budget.status(db, budget.descriptor(frozen))
        assert state["actual_tokens"] == 50 and state["reserved_tokens"] == 200
        assert "call_allowance" not in frozen and len(frozen["previous_call_allowances"]) == 1
        assert before == [
            tuple(r) for r in db.execute("select * from pi_processing_runs order by run_id")
        ]


def test_zero_budget_blocks_before_key_reservation(config, policy, bound):
    selected, runs, _ = prepared(config, policy, bound, limit=0)
    with database.connect(config.state_root) as db:
        with pytest.raises(Exhausted):
            execution_ledger.reserve(config, db, runs[0], selected)
        assert db.execute("select count(*) from model_calls").fetchone()[0] == 0
        assert db.execute("select count(*) from model_budget_reservations").fetchone()[0] == 0


def test_key_budget_still_limits_a_video_with_available_tokens(config, policy, bound):
    selected, runs, frozen = prepared(config, policy, bound, limit=500_000)
    with database.connect(config.state_root) as db:
        identity = model_budgets.key(db, selected.secret_file)
        row = model_budgets.get(db, identity)
        settings = dict(row["settings"], limit=100)
        db.execute(
            "update model_budget_policies set value=? where credential_id=?",
            (json.dumps(settings), identity),
        )
        with pytest.raises(Exhausted):
            execution_ledger.reserve(config, db, runs[0], selected)
        assert budget.status(db, budget.descriptor(frozen))["remaining_tokens"] == 500_000
        assert db.execute("select count(*) from model_calls").fetchone()[0] == 0


def test_separate_videos_do_not_share_the_per_video_limit(config, policy, bound):
    selected, runs, frozen = prepared(config, policy, bound, limit=200)
    other = {"job_id": "another-video", "token_budget": {"limit": 200}}
    budget.configure(other)
    with database.connect(config.state_root) as db:
        budget.bind(db, runs[1], other, 200)
        execution_ledger.reserve(config, db, runs[0], selected)
        execution_ledger.reserve(config, db, runs[1], selected)
        assert budget.status(db, budget.descriptor(frozen))["used_tokens"] == 200
        assert budget.status(db, budget.descriptor(other))["used_tokens"] == 200


@pytest.mark.parametrize("value", [True, -1, 0.5, "500000", 1_000_000_001])
def test_strict_token_limits(value):
    with pytest.raises(ValidationError):
        Retry(expected_revision=1, max_video_tokens=value)
    with pytest.raises(ValidationError):
        Settings(max_video_tokens=value)
    with pytest.raises(InvalidInput):
        budget.validate_limit(value)


def test_default_is_half_million_and_existing_frozen_limit_is_preserved():
    assert Settings().max_video_tokens == 500_000
    frozen = {"job_id": "video", "skill": {"settings": {"max_video_tokens": 100}}}
    budget.configure(frozen)
    frozen["skill"]["settings"]["max_video_tokens"] = 500_000
    budget.configure(frozen)
    assert budget.descriptor(frozen)["limit"] == 100
