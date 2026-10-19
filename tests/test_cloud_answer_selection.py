import copy
import json
from dataclasses import replace
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from test_cloud_answer_tasks import cloud_task as cloud_task
from test_cloud_answer_tasks import result
from test_local_query_tasks import local_task as local_task
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner

from fiber_bundle import (
    auth,
    cloud_answer_tasks,
    cloud_answer_worker,
    database,
)
from fiber_bundle import cloud_answer_gateway as gateway
from fiber_bundle import cloud_answer_selection as selection
from fiber_bundle.config import InvalidInput
from fiber_bundle.model_gateway import ModelFailure
from fiber_bundle.query_routing_config import parse


@pytest.fixture
def route(routing, request):
    for i in (1, 2):
        backup = copy.deepcopy(routing["cloud_answer"])
        backup.update(
            model=f"synthetic-backup-{i}",
            input_nusd=10 * i,
            output_nusd=20 * i,
            daily_calls=100,
            daily_limit_nusd=10_000_000,
        )
        routing["approved_backups"].append(backup)
    variant = getattr(request, "param", None)
    first = routing["approved_backups"][0]
    if variant == "expensive":
        first.update(input_nusd=1000, output_nusd=1000, task_limit_nusd=2_000_000)
    elif variant == "request":
        first["max_request_bytes"] = 128
    elif variant == "timezone":
        first["timezone"] = "Asia/Taipei"
    elif variant == "lower-task":
        first["task_limit_nusd"] = (
            first["max_input_tokens"] * first["input_nusd"]
            + first["max_output_tokens"] * first["output_nusd"]
        )
    return parse(routing)


def choose(config, route, owner, run, index=1, nonce="choice-one"):
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            return selection.select(
                config, db, route, owner[0], run, index, nonce, allow_switch=True
            )
        finally:
            db.close()


def test_default_refuses_before_any_io():
    with patch("sqlite3.connect", side_effect=AssertionError("IO")):
        with pytest.raises(InvalidInput, match="not_enabled"):
            selection.select(None, None, None, None, None, None, None)


@pytest.mark.parametrize("index", [1, 2])
def test_explicit_selection_is_durable_idempotent_and_uses_only_selected_policy(
    config, route, owner, cloud_task, index
):
    with (
        patch("subprocess.Popen", side_effect=AssertionError("process")),
        patch(
            "fiber_bundle.execution_config.read_secret", side_effect=AssertionError("credential")
        ),
    ):
        selected = choose(config, route, owner, cloud_task, index)
        assert selected["state"] == "selected" and selected["automatic_switch"] is False
        assert choose(config, route, owner, cloud_task, index) == selected
    assert len(selected["selections"]) == 1
    audit = selected["selections"][0]
    assert audit["selected_digest"] == route.answer_policy(index).digest
    assert audit["previous_digest"] == route.answer_policy().digest
    assert audit["approval_kind"] == "trusted_host_explicit"
    with patch.object(gateway, "complete", return_value=result()) as provider:
        batch = cloud_answer_worker.dispatch(
            config, route, owner[0], max_tasks=1, allow_inference=True
        )
        assert batch["state"] == "limit_reached"
        assert provider.call_count == 1 and provider.call_args.args[0] == route.answer_policy(index)
        assert set(provider.call_args.args[2]) == {"schema", "answer"}
        assert "What pressure?" not in json.dumps(provider.call_args.args[2])
    db = database.connect(config.state_root)
    call = db.execute("SELECT * FROM model_calls").fetchone()
    assert call["model"] == route.answer_policy(index).model and call["state"] == "settled"
    assert (
        call["charged_nusd"]
        == 20 * route.answer_policy(index).input_nusd + 30 * route.answer_policy(index).output_nusd
    )
    assert (
        cloud_answer_tasks.deliver(config, db, owner[0], cloud_task)["cloud_answer"][
            "semantic_verified"
        ]
        is False
    )
    db.close()
    assert (
        choose(config, route, owner, cloud_task, index)["state"] == "selected"
    )  # No second choice/call.
    assert (
        cloud_answer_worker.dispatch(config, route, owner[0], max_tasks=1, allow_inference=True)[
            "state"
        ]
        == "idle"
    )


@pytest.mark.parametrize("index", [True, False, 0, -1, 3, 1.0, "1"])
def test_undeclared_or_invalid_choice_never_mutates_or_calls(
    config, route, owner, cloud_task, index
):
    with patch.object(gateway, "complete", side_effect=AssertionError("model")):
        with pytest.raises(InvalidInput):
            choose(config, route, owner, cloud_task, index)
    db = database.connect(config.state_root)
    assert db.execute("SELECT count(*) FROM cloud_answer_selections").fetchone()[0] == 0
    assert (
        db.execute("SELECT config_digest FROM execution_runs").fetchone()[0]
        == route.answer_policy().digest
    )
    db.close()


@pytest.mark.parametrize("mutation", ["foreign", "revoke", "evidence", "route"])
def test_authority_and_whole_route_are_rechecked(config, route, owner, cloud_task, mutation):
    db = database.connect(config.state_root)
    if mutation == "foreign":
        owner = ("foreign", *owner[1:])
    elif mutation == "revoke":
        auth.revoke_client(db, owner[0])
    elif mutation == "evidence":
        db.execute("DELETE FROM session_refs")
    else:
        route = replace(route, approved_backups=tuple(reversed(route.approved_backups)))
    db.close()
    with pytest.raises((auth.Denied, InvalidInput)):
        choose(config, route, owner, cloud_task)
    db = database.connect(config.state_root)
    assert db.execute("SELECT count(*) FROM cloud_answer_selections").fetchone()[0] == 0
    db.close()


@pytest.mark.parametrize("state", ["running", "failed", "cancelled", "interrupted", "succeeded"])
def test_prior_attempt_or_terminal_state_refused_without_replay(
    config, route, owner, cloud_task, state
):
    db = database.connect(config.state_root)
    db.execute("UPDATE execution_runs SET state=?,attempts=1", (state,))
    db.close()
    refusal = choose(config, route, owner, cloud_task)
    assert refusal["state"] == "refused" and refusal["reason"] == "cloud_switch_already_attempted"
    assert refusal["selections"][0]["state"] == "refused"
    db = database.connect(config.state_root)
    assert db.execute("SELECT state FROM execution_runs").fetchone()[0] == state
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    db.close()


def test_second_choice_and_conflicting_nonce_refused(config, route, owner, cloud_task):
    choose(config, route, owner, cloud_task)
    with pytest.raises(InvalidInput, match="conflict"):
        choose(config, route, owner, cloud_task, 2)
    refusal = choose(config, route, owner, cloud_task, 2, "new-choice")
    assert refusal["reason"] == "cloud_backup_already_selected"
    assert len(refusal["selections"]) == 2


@pytest.mark.parametrize("kind", ["uncertain", "settled"])
def test_daily_budget_cannot_expand_by_backup(config, route, owner, cloud_task, kind):
    db = database.connect(config.state_root)
    binding = db.execute(
        "SELECT local_task_id FROM cloud_answer_tasks WHERE run_id=?", (cloud_task,)
    ).fetchone()
    other = cloud_answer_tasks.submit(config, db, route, owner[0], binding[0], "prior-fees")[
        "run_id"
    ]
    day = datetime.now(ZoneInfo(route.answer_policy().timezone)).date().isoformat()
    for i in range(2):
        db.execute(
            "INSERT INTO model_calls(id,run_id,model,price_revision,billing_day,currency,"
            "reserved_nusd,charged_nusd,state,created_at) VALUES(?,?,?,?,?,'USD',?,?,?,0)",
            (
                f"old-{i}",
                other,
                "old-model",
                "synthetic",
                day,
                100,
                1 if kind == "settled" else None,
                kind,
            ),
        )
    db.close()
    refused = choose(config, route, owner, cloud_task)
    assert refused["reason"] == "cloud_backup_budget_exceeded"
    db = database.connect(config.state_root)
    assert (
        db.execute("SELECT config_digest FROM execution_runs WHERE id=?", (cloud_task,)).fetchone()[
            0
        ]
        == route.answer_policy().digest
    )
    assert (
        db.execute("SELECT attempts FROM execution_runs WHERE id=?", (cloud_task,)).fetchone()[0]
        == 0
    )
    db.close()


def test_budget_is_checked_again_after_selection(config, route, owner, cloud_task):
    choose(config, route, owner, cloud_task)
    db = database.connect(config.state_root)
    binding = db.execute(
        "SELECT local_task_id FROM cloud_answer_tasks WHERE run_id=?", (cloud_task,)
    ).fetchone()
    other = cloud_answer_tasks.submit(config, db, route, owner[0], binding[0], "spent-later")[
        "run_id"
    ]
    day = datetime.now(ZoneInfo(route.answer_policy().timezone)).date().isoformat()
    db.execute(
        "INSERT INTO model_calls(id,run_id,model,price_revision,billing_day,currency,"
        "reserved_nusd,state,created_at) VALUES('day-cost',?,'old','synthetic',?,"
        "'USD',2000000,'uncertain',0)",
        (other, day),
    )
    db.close()
    with patch.object(gateway, "complete", side_effect=AssertionError("model")):
        assert (
            cloud_answer_worker.dispatch(
                config, route, owner[0], max_tasks=1, allow_inference=True
            )["state"]
            == "blocked"
        )
    db = database.connect(config.state_root)
    assert (
        db.execute("SELECT count(*) FROM model_calls WHERE run_id=?", (cloud_task,)).fetchone()[0]
        == 0
    )
    db.close()


@pytest.mark.parametrize(
    "reason", ["cloud_timeout", "cloud_transport_failed", "cloud_response_rejected"]
)
def test_backup_unavailable_or_rejected_stops_and_preserves_selection(
    config, route, owner, cloud_task, reason
):
    choose(config, route, owner, cloud_task)
    with patch.object(gateway, "complete", side_effect=ModelFailure(reason)) as provider:
        batch = cloud_answer_worker.dispatch(
            config, route, owner[0], max_tasks=2, allow_inference=True
        )
        assert batch["state"] == "stopped" and provider.call_count == 1
        assert provider.call_args.args[0] == route.answer_policy(1)
    db = database.connect(config.state_root)
    status = cloud_answer_tasks.status(db, owner[0], cloud_task)
    assert status["error_code"] == reason and status["cloud_call"]["state"] == "uncertain"
    assert status["selections"][0]["state"] == "selected"
    db.close()
    assert (
        choose(config, route, owner, cloud_task, 2, "after-failure")["reason"]
        == "cloud_switch_already_attempted"
    )
    assert (
        cloud_answer_worker.dispatch(config, route, owner[0], max_tasks=1, allow_inference=True)[
            "state"
        ]
        == "idle"
    )


@pytest.mark.parametrize(
    "route,reason",
    [
        ("expensive", "cloud_backup_budget_exceeded"),
        ("request", "cloud_backup_request_rejected"),
        ("timezone", "cloud_backup_timezone_mismatch"),
    ],
    indirect=["route"],
)
def test_backup_boundaries_are_checked_on_the_frozen_route(
    config, route, owner, cloud_task, reason
):
    report = choose(config, route, owner, cloud_task)
    assert report["state"] == "refused" and report["reason"] == reason
    db = database.connect(config.state_root)
    assert (
        db.execute("SELECT config_digest FROM execution_runs").fetchone()[0]
        == route.answer_policy().digest
    )
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    db.close()


@pytest.mark.parametrize("route", ["lower-task"], indirect=True)
def test_selected_lower_task_ceiling_is_retained(config, route, owner, cloud_task):
    assert choose(config, route, owner, cloud_task)["state"] == "selected"
    db = database.connect(config.state_root)
    assert (
        db.execute("SELECT task_limit_nusd FROM execution_runs").fetchone()[0]
        == route.answer_policy(1).task_limit_nusd
    )
    db.close()


@pytest.mark.parametrize("point", ["reserve", "settle", "commit"])
def test_selected_backup_keeps_interruption_and_accounting_contract(
    config, route, owner, cloud_task, point
):
    from test_cloud_answer_interruptions import (
        test_interrupt_at_every_durable_boundary_preserves_attempt_and_known_usage,
    )

    choose(config, route, owner, cloud_task)
    test_interrupt_at_every_durable_boundary_preserves_attempt_and_known_usage(
        config, route, owner, cloud_task, point
    )
    db = database.connect(config.state_root)
    if point in ("settle", "commit"):
        assert db.execute("SELECT charged_nusd FROM model_calls").fetchone()[0] == 800
    db.close()


def test_selection_commit_failure_rolls_back_policy_and_audit(config, route, owner, cloud_task):
    import sqlite3

    class Fault(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.strip() == "COMMIT":
                raise sqlite3.OperationalError("synthetic commit failure")
            return super().execute(sql, parameters)

    db = sqlite3.connect(
        config.state_root / "metadata.sqlite3", isolation_level=None, factory=Fault
    )
    db.row_factory = sqlite3.Row
    with pytest.raises(sqlite3.OperationalError):
        selection.select(
            config, db, route, owner[0], cloud_task, 1, "commit-fault", allow_switch=True
        )
    assert (
        db.execute("SELECT config_digest FROM execution_runs").fetchone()[0]
        == route.answer_policy().digest
    )
    assert db.execute("SELECT count(*) FROM cloud_answer_selections").fetchone()[0] == 0
    db.close()


def test_real_selected_credential_child_failure_stops_without_fallback(
    config, route, owner, cloud_task
):
    import subprocess

    choose(config, route, owner, cloud_task)
    children = []
    original = subprocess.Popen

    def spawn(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child

    with patch("subprocess.Popen", side_effect=spawn):
        batch = cloud_answer_worker.dispatch(
            config, route, owner[0], max_tasks=2, allow_inference=True
        )
    assert (
        batch["state"] == "stopped"
        and batch["results"][0]["error_code"] == "cloud_transport_failed"
    )
    assert len(children) == 1 and children[0].poll() is not None
    db = database.connect(config.state_root)
    call = db.execute("SELECT model,state FROM model_calls").fetchone()
    assert tuple(call) == (route.answer_policy(1).model, "uncertain")
    assert (
        cloud_answer_tasks.status(db, owner[0], cloud_task)["selections"][0]["state"] == "selected"
    )
    db.close()


def test_daily_ceiling_and_reservation_use_one_billing_day(config, route, owner, cloud_task):
    from fiber_bundle import execution_ledger

    choose(config, route, owner, cloud_task)
    db = database.connect(config.state_root)
    tid = db.execute(
        "SELECT local_task_id FROM cloud_answer_tasks WHERE run_id=?", (cloud_task,)
    ).fetchone()[0]
    other = cloud_answer_tasks.submit(config, db, route, owner[0], tid, "midnight-existing")[
        "run_id"
    ]
    for i in range(2):
        db.execute(
            "INSERT INTO model_calls(id,run_id,model,price_revision,billing_day,currency,"
            "reserved_nusd,state,created_at) "
            "VALUES(?,?,'old','synthetic','2026-01-02','USD',1,'uncertain',0)",
            (f"midnight-{i}", other),
        )
    db.close()
    dates = iter(
        datetime.fromisoformat(d).replace(tzinfo=ZoneInfo("UTC"))
        for d in ["2026-01-01", "2026-01-02", "2026-01-03"]
    )

    class Clock:
        @staticmethod
        def now(zone):
            return next(dates)

    with (
        patch.object(execution_ledger, "datetime", Clock),
        patch.object(gateway, "complete", return_value=result()),
    ):
        assert (
            cloud_answer_worker.dispatch(
                config, route, owner[0], max_tasks=1, allow_inference=True
            )["state"]
            == "limit_reached"
        )
    db = database.connect(config.state_root)
    counts = db.execute(
        "SELECT billing_day,count(*) FROM model_calls GROUP BY billing_day"
    ).fetchall()
    assert all(row[1] <= route.answer_policy().daily_calls for row in counts)
    db.close()
