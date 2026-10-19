import json

import pytest
from test_model_ledger import configured

from fiber_bundle import database, model_budgets, model_gateway
from fiber_bundle.organization import model_evidence, model_review
from fiber_bundle.organization import store as ledger

pytest_plugins = ["test_execution"]


def setup(config, policy, monkeypatch, *, bad=False):
    selected = configured(config, policy)
    monkeypatch.setattr(model_review, "route", lambda *a: selected)
    monkeypatch.setattr(model_gateway, "prepare", lambda p, v: v)
    calls = []

    def complete(*args):
        calls.append(args)
        raw = json.dumps(
            {
                "moc_id": "heat",
                "evidence": [
                    "固体内部的热量通过温度梯度传递。",
                    "冷却液通过流动将热量带离高温区域。",
                ],
                "scope_quote": "模型编造的主题定义。" if bad else "热怎么传出去:TIM、冷板、液冷。",
                "reason": "传热及冷却为主要主题。",
            },
            ensure_ascii=False,
        )
        return {
            "usage": {"prompt_tokens": 21, "completion_tokens": 12},
            "choices": [{"message": {"content": raw}}],
        }

    monkeypatch.setattr(model_gateway, "complete", complete)
    job = {"id": "job", "vault_id": "vault"}
    entity = {
        "title": "传热",
        "text": "# 资料\n固体内部的热量通过温度梯度传递。\n冷却液通过流动将热量带离高温区域。\n",
    }
    mocs = [
        {
            "id": "heat",
            "title": "热管理 MOC",
            "path": "Map/热管理 MOC.md",
            "text": "# 热管理 MOC\n热怎么传出去：TIM、冷板、液冷。",
        }
    ]
    return (job, entity, mocs, {"fingerprint": "fixed"}, lambda: None), calls


def resolve(config, args):
    return model_review.resolve(config, *args)


def test_transport_and_evidence_success_are_separate_and_repeat_does_not_charge(
    config, store, policy, monkeypatch
):
    args, calls = setup(config, policy, monkeypatch)
    result = resolve(config, args)
    assert result["selected"] == ["heat"]
    row = ledger.rows(config, "model_reviews")[0]
    assert row["state"] == "succeeded"
    assert row["evidence_validation"]["state"] == "validated"
    again = resolve(config, args)
    assert again["model_response_reused"] == 1 and not again["model_called"]
    assert len(calls) == 1
    assert ledger.rows(config, "model_reviews")[0] == row
    with database.connect(config.state_root) as db:
        assert model_budgets.inspect(db)[0]["used"] == 33
        assert db.execute("SELECT count(*) FROM model_configuration_calls").fetchone()[0] == 1


@pytest.mark.parametrize("old_marker", [None, {"state": "invalid", "version": "legacy"}])
def test_old_cached_reply_is_revalidated_in_place_without_changing_raw_reply_or_usage(
    config, store, policy, monkeypatch, old_marker
):
    args, calls = setup(config, policy, monkeypatch)
    resolve(config, args)
    with ledger.connection(config) as db:
        key = db.execute("SELECT id FROM model_reviews").fetchone()[0]
    row = ledger.get(config, "model_reviews", key)
    if old_marker is None:
        row.pop("evidence_validation")
    else:
        row["evidence_validation"] = old_marker
    ledger.put(config, "model_reviews", key, "vault", row)
    monkeypatch.setattr(
        model_gateway, "complete", lambda *a: pytest.fail("must reuse legacy reply")
    )
    result = resolve(config, args)
    after = ledger.get(config, "model_reviews", key)
    assert after["evidence_validation"]["state"] == "validated"
    assert {k: v for k, v in after.items() if k != "evidence_validation"} == {
        k: v for k, v in row.items() if k != "evidence_validation"
    }
    assert result["selected"] == ["heat"] and len(calls) == 1


def test_invalid_evidence_is_recorded_but_never_resubmitted_or_accepted(
    config, store, policy, monkeypatch
):
    args, calls = setup(config, policy, monkeypatch, bad=True)
    for _ in range(2):
        with pytest.raises(model_evidence.EvidenceInvalid) as error:
            resolve(config, args)
        assert error.value.reason == "scope_quote_missing"
    row = ledger.rows(config, "model_reviews")[0]
    assert row["state"] == "succeeded"
    assert row["evidence_validation"]["state"] == "invalid"
    assert row["evidence_validation"]["reason"] == "scope_quote_missing"
    assert len(calls) == 1
    with database.connect(config.state_root) as db:
        assert model_budgets.inspect(db)[0]["used"] == 33


def test_cached_only_validation_never_sends_or_rewrites_usage(config, store, policy, monkeypatch):
    args, calls = setup(config, policy, monkeypatch)
    result = resolve(config, args)
    rows = ledger.rows(config, "model_reviews")
    monkeypatch.setattr(
        model_gateway, "complete", lambda *a: pytest.fail("cache-only must never send")
    )
    assert model_review.resolve(config, *args, cached_only=True)["selected"] == result["selected"]
    assert ledger.rows(config, "model_reviews") == rows and len(calls) == 1


def test_explicit_completion_reuses_old_windows_and_only_sends_missing_windows(
    config, store, policy, monkeypatch
):
    args, _ = setup(config, policy, monkeypatch)
    from dataclasses import replace

    selected = replace(model_review.route(config, None, "vault"), max_input_tokens=4000)
    monkeypatch.setattr(model_review, "route", lambda *a: selected)
    job, entity, mocs, item, check = args
    entity["text"] = "\n".join(
        f"第{i}段正文描述温度梯度。" + "冷却液参与流动传热。" * 20 for i in range(160)
    )
    keys = []
    real_call = model_review.call

    def reply(messages):
        parts = json.loads(messages[-1]["content"])["passages"]
        return json.dumps(
            {
                "moc_id": "heat",
                "evidence": [parts[0]["text"], parts[1]["text"]],
                "scope_quote": "热怎么传出去：TIM、冷板、液冷。",
                "reason": "传热主题",
            }
        )

    def seed(config, job, selected, messages, key, check):
        raw = reply(messages)
        keys.append(key)
        ledger.put(
            config,
            "model_reviews",
            key,
            "vault",
            {"id": "historical", "state": "succeeded", "reply": raw, "usage": {"total_tokens": 12}},
        )
        return raw

    monkeypatch.setattr(model_review, "call", seed)
    model_review.resolve(config, *args)
    assert len(keys) > 2
    kept = ledger.get(config, "model_reviews", keys[0])
    with ledger.connection(config, write=True) as db:
        db.executemany("DELETE FROM model_reviews WHERE id=?", [(key,) for key in keys[1:]])
    with pytest.raises(model_review.CacheIncomplete) as caught:
        model_review.resolve(config, *args, cached_only=True)
    assert caught.value.detail == {"completed_windows": 1, "total_windows": len(keys)}
    sent = []

    def complete(selected, identity, payload):
        sent.append(payload)
        return {
            "usage": {"prompt_tokens": 3, "completion_tokens": 4},
            "choices": [{"message": {"content": reply(payload["messages"])}}],
        }

    monkeypatch.setattr(model_review, "call", real_call)
    monkeypatch.setattr(model_gateway, "complete", complete)
    result = model_review.resolve(config, *args)
    assert result["selected"] == ["heat"] and len(sent) == len(keys) - 1
    assert ledger.get(config, "model_reviews", keys[0]) == kept
    sent.clear()
    assert model_review.resolve(config, *args, cached_only=True)["selected"] == ["heat"]
    assert not sent
