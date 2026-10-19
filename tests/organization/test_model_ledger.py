from dataclasses import asdict

import pytest

from fiber_bundle import database, model_budgets, model_gateway
from fiber_bundle.config import InvalidInput
from fiber_bundle.model_configuration.policy import RegisteredPolicy
from fiber_bundle.organization import model_review
from fiber_bundle.organization import store as organization
from fiber_bundle.organization.model_unknown import UnknownCall

pytest_plugins = ["test_execution"]


def configured(config, policy):
    selected = RegisteredPolicy(
        **{**asdict(policy), "max_output_tokens": 8192}, configuration_id="synthetic-config"
    )
    with database.connect(config.state_root) as db:
        db.execute(
            "INSERT INTO model_credentials VALUES('synthetic-key',1,1,'{}','synthetic',?,1,0)",
            (policy.secret_file,),
        )
        db.execute(
            "INSERT INTO model_configurations "
            "VALUES('synthetic-config',1,'synthetic-key','{}',1,NULL,0)"
        )
        model_budgets.save(
            db,
            model_budgets.Save(
                secret_file=policy.secret_file,
                expected_revision=0,
                settings=model_budgets.Policy(label="test budget", limit=1000000),
            ),
        )
    return selected


def test_shared_reservation_settlement_and_cached_repeat(config, store, policy, monkeypatch):
    selected = configured(config, policy)
    calls = []

    def complete(*args):
        calls.append(args)
        return {
            "usage": {"prompt_tokens": 21, "completion_tokens": 12},
            "choices": [{"message": {"content": '{"moc_id":null}'}}],
        }

    monkeypatch.setattr(model_gateway, "prepare", lambda p, v: v)
    monkeypatch.setattr(model_gateway, "complete", complete)
    job = {"id": "classification-job", "vault_id": "synthetic-vault"}
    for _ in range(2):
        assert model_review.call(
            config, job, selected, [{"role": "user", "content": "synthetic"}], "same", lambda: None
        )
    assert len(calls) == 1
    with database.connect(config.state_root) as db:
        assert model_budgets.inspect(db)[0]["used"] == 33
        row = db.execute("SELECT * FROM model_configuration_calls").fetchone()
        assert (
            row["state"] == "succeeded" and row["input_tokens"] == 21 and row["output_tokens"] == 12
        )
    assert organization.get(config, "model_reviews", "same")["state"] == "succeeded"


def test_unknown_result_keeps_budget_and_never_resends(config, store, policy, monkeypatch):
    selected = configured(config, policy)
    calls = []

    def complete(*args):
        calls.append(args)
        raise TimeoutError("synthetic timeout")

    monkeypatch.setattr(model_gateway, "prepare", lambda p, v: v)
    monkeypatch.setattr(model_gateway, "complete", complete)
    job = {"id": "classification-job", "vault_id": "synthetic-vault"}
    with pytest.raises(UnknownCall) as caught:
        model_review.call(config, job, selected, [], "same", lambda: None)
    assert isinstance(caught.value.__cause__, TimeoutError)
    assert organization.get(config, "model_reviews", "same")["state"] == "unknown"
    with pytest.raises(InvalidInput, match="result_unknown"):
        model_review.call(config, job, selected, [], "same", lambda: None)
    assert len(calls) == 1
    with database.connect(config.state_root) as db:
        assert (
            model_budgets.inspect(db)[0]["used"]
            == selected.max_input_tokens + selected.max_output_tokens
        )


def test_exhausted_unified_budget_does_not_send(config, store, policy, monkeypatch):
    selected = configured(config, policy)
    with database.connect(config.state_root) as db:
        saved = model_budgets.inspect(db)[0]
        model_budgets.save(
            db,
            model_budgets.Save(
                secret_file=policy.secret_file,
                expected_revision=saved["revision"],
                settings=model_budgets.Policy(label="test budget", limit=1),
            ),
        )
    monkeypatch.setattr(model_gateway, "prepare", lambda p, v: v)
    monkeypatch.setattr(model_gateway, "complete", lambda *a: pytest.fail("must not send"))
    with pytest.raises(model_budgets.Exhausted):
        model_review.call(
            config, {"id": "job", "vault_id": "vault"}, selected, [], "blocked", lambda: None
        )
    with database.connect(config.state_root) as db:
        assert db.execute("SELECT count(*) FROM model_configuration_calls").fetchone()[0] == 0


def test_known_length_limited_response_settles_usage_then_expands_window(
    config, store, policy, monkeypatch
):
    # This verifies settlement, not calendar rollover. Keep the cycle stable
    # when the two synthetic calls happen to straddle local midnight.
    monkeypatch.setattr(model_budgets, "period", lambda _: "2026-10-17")
    selected = configured(config, policy)
    limits = []
    monkeypatch.setattr(model_gateway, "prepare", lambda p, v: v)

    def complete(p, *args):
        limits.append(p.max_output_tokens)
        return {
            "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            "choices": [
                {
                    "finish_reason": "length" if len(limits) == 1 else "stop",
                    "message": {"content": None if len(limits) == 1 else '{"moc_id":null}'},
                }
            ],
        }

    monkeypatch.setattr(model_gateway, "complete", complete)
    job = {"id": "job", "vault_id": "vault"}
    assert model_review.call(config, job, selected, [], "length", lambda: None) == '{"moc_id":null}'
    assert limits == [selected.max_output_tokens, selected.max_output_tokens * 2]
    with database.connect(config.state_root) as db:
        assert model_budgets.inspect(db)[0]["used"] == 40
        rows = db.execute(
            "SELECT state,input_tokens,output_tokens FROM model_configuration_calls"
        ).fetchall()
        assert len(rows) == 2 and all(
            r["input_tokens"] == 10 and r["output_tokens"] == 10 for r in rows
        )
    saved = organization.get(config, "model_reviews", "length")
    assert saved["state"] == "succeeded" and saved["attempts"][0]["state"] == "invalid_response"
