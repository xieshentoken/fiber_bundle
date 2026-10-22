"""Synthetic interruptions must not repeat a successful paid query stage."""

import json
from contextlib import contextmanager

import pytest
from test_channel_natural_query import envelope, result
from test_channel_natural_query import model_channel as model_channel
from test_message_channels import channel as channel
from test_model_configuration import vault_keychain as vault_keychain

from fiber_bundle import database, model_gateway
from fiber_bundle.channels import dispatch, model_output


def responding(monkeypatch):
    calls = []

    def complete(policy, identity, payload):
        calls.append(identity)
        if len(calls) == 1:
            return envelope({"queries": ["青松"]})
        packet = json.loads(payload["messages"][1]["content"])
        return envelope(
            {
                "claims": [{"text": "压力为2.4 MPa。", "citations": ["[1]"]}],
                "quotations": packet["quotations"],
            }
        )

    monkeypatch.setattr(model_gateway, "complete", complete)
    return calls


def test_final_storage_interruption_recovers_saved_answer_without_new_calls(
    config, model_channel, monkeypatch
):
    value, _ = model_channel
    calls = responding(monkeypatch)
    dispatch.capture(
        config, value["id"], value["revision"], "synthetic", "ou_synthetic", "介绍青松"
    )
    original_lock = database.locked
    original_accept = model_output.accept
    remaining = 0

    def accept(*args):
        nonlocal remaining
        accepted = original_accept(*args)
        remaining = 2  # verification succeeds; the final event write cannot acquire storage
        return accepted

    @contextmanager
    def unavailable(config):
        nonlocal remaining
        if remaining:
            remaining -= 1
            if not remaining:
                raise database.LockTimeout("synthetic storage contention")
        with original_lock(config):
            yield

    monkeypatch.setattr(model_output, "accept", accept)
    monkeypatch.setattr(database, "locked", unavailable)
    with pytest.raises(database.LockTimeout):
        dispatch.advance(config, value["id"], value["revision"])
    assert result(config, value)[0] == "processing"
    assert len(calls) == 2
    monkeypatch.setattr(database, "locked", original_lock)
    monkeypatch.setattr(model_output, "accept", original_accept)
    dispatch.advance(config, value["id"], value["revision"])
    state, data = result(config, value)
    assert state == "ready"
    assert data["result"].get("citation_integrity") == "verified"
    assert "2.4 MPa" in data["result"]["answer"]
    assert len(calls) == 2


def interrupt_after(monkeypatch, stage):
    from fiber_bundle.channels import model_call

    original = model_call.complete
    pending = True

    def complete(*args):
        nonlocal pending
        answer = original(*args)
        if pending and args[3] == stage:
            pending = False
            raise database.LockTimeout("synthetic interruption after durable response")
        return answer

    monkeypatch.setattr(model_call, "complete", complete)


def capture(config, value):
    assert dispatch.capture(
        config, value["id"], value["revision"], "synthetic", "ou_synthetic", "介绍青松"
    )


@pytest.mark.parametrize("platform", ["feishu", "weixin"])
@pytest.mark.parametrize("stage", ["retrieval", "answer"])
def test_saved_stage_resumes_with_original_call_ids_and_single_budget_charge(
    config, model_channel, monkeypatch, stage, platform
):
    import asyncio

    from fiber_bundle import model_budgets
    from fiber_bundle.channels import store

    value, _ = model_channel
    with store.opened(config) as db:
        current = store.get(db, value["id"])
        current["value"]["platform"] = platform
        db.execute(
            "UPDATE channels SET value=? WHERE id=?", (json.dumps(current["value"]), value["id"])
        )
    calls = responding(monkeypatch)
    capture(config, value)
    interrupt_after(monkeypatch, stage)
    with pytest.raises(database.LockTimeout):
        dispatch.advance(config, value["id"], value["revision"])
    state, data = result(config, value)
    assert state == "processing" and "failure" not in data
    assert data["query_processing"]["phase"] in {"retrieval_call", "answer_call"}
    dispatch.advance(config, value["id"], value["revision"])
    assert result(config, value)[1]["result"]["citation_integrity"] == "verified"
    assert len(calls) == len(set(calls)) == 2
    with database.connect(config.state_root) as db:
        assert model_budgets.inspect(db)[0]["used"] == 300
        assert db.execute("SELECT count(*) FROM model_budget_reservations").fetchone()[0] == 2
        assert (
            db.execute(
                "SELECT count(*) FROM model_configuration_calls WHERE state='succeeded'"
            ).fetchone()[0]
            == 2
        )
    sent = []

    async def sender(owner, body, context, guard):
        guard()
        sent.append(body)
        return True

    assert asyncio.run(dispatch.deliver(config, value["id"], value["revision"], sender)) == "sent"
    saved = result(config, value)
    dispatch.advance(config, value["id"], value["revision"])
    assert asyncio.run(dispatch.deliver(config, value["id"], value["revision"], sender)) == "idle"
    assert result(config, value) == saved
    assert len(calls) == 2 and len(sent) == 1


@pytest.mark.parametrize("failure", ["lock", "sqlite_busy", "process_exit"])
def test_response_is_durable_before_settlement_and_resumes_without_reserving_again(
    config, model_channel, monkeypatch, failure
):
    import sqlite3

    from fiber_bundle import model_budgets

    class ProcessExit(BaseException):
        pass

    value, _ = model_channel
    calls = responding(monkeypatch)
    capture(config, value)
    original = model_budgets.settle
    pending = True

    def settle(*args):
        nonlocal pending
        if pending:
            pending = False
            if failure == "process_exit":
                raise ProcessExit()
            if failure == "sqlite_busy":
                raise sqlite3.OperationalError("database is locked")
            raise database.LockTimeout("synthetic budget storage busy")
        return original(*args)

    monkeypatch.setattr(model_budgets, "settle", settle)
    with pytest.raises(ProcessExit if failure == "process_exit" else database.LockTimeout):
        dispatch.advance(config, value["id"], value["revision"])
    assert result(config, value)[0] == "processing"
    with database.connect(config.state_root) as db:
        row = db.execute("SELECT * FROM model_configuration_calls").fetchone()
        assert row["state"] == "running"
        reservation = row["reservation_id"]
    cached = list((config.state_root / "channel-query-results").glob("*.json"))
    assert len(cached) == 1 and cached[0].stat().st_mode & 0o777 == 0o600
    dispatch.advance(config, value["id"], value["revision"])
    assert result(config, value)[1]["result"]["citation_integrity"] == "verified"
    assert len(calls) == 2
    with database.connect(config.state_root) as db:
        assert db.execute("SELECT count(*) FROM model_budget_reservations").fetchone()[0] == 2
        assert db.execute(
            "SELECT state,charged FROM model_budget_reservations WHERE id=?", (reservation,)
        ).fetchone()[:] == ("settled", 150)
        assert model_budgets.inspect(db)[0]["used"] == 300


@pytest.mark.parametrize("stage", ["retrieval", "answer"])
@pytest.mark.parametrize(
    "damage", ["missing", "corrupt", "request", "policy", "result", "legacy", "public"]
)
def test_unverified_response_is_never_recalled_or_delivered(
    config, model_channel, monkeypatch, stage, damage
):
    value, _ = model_channel
    calls = responding(monkeypatch)
    capture(config, value)
    interrupt_after(monkeypatch, stage)
    with pytest.raises(database.LockTimeout):
        dispatch.advance(config, value["id"], value["revision"])
    cached = config.state_root / "channel-query-results" / (calls[-1] + ".json")
    if damage == "missing":
        cached.unlink()
    elif damage == "corrupt":
        cached.write_text("{interrupted")
    elif damage == "public":
        cached.chmod(0o644)
    else:
        data = json.loads(cached.read_text())
        if damage == "legacy":
            data = data["result"]
        elif damage == "result":
            data["result"]["usage"]["completion_tokens"] += 1
        else:
            data[damage + "_sha256"] = "0" * 64
        cached.write_text(json.dumps(data))
    count = len(calls)
    dispatch.advance(config, value["id"], value["revision"])
    state, data = result(config, value)
    assert state == "ready" and data["error_code"] == "channel_model_result_unknown"
    assert data["result"]["sources"] == [] and len(calls) == count
    terminal = result(config, value)
    dispatch.advance(config, value["id"], value["revision"])
    assert result(config, value) == terminal and len(calls) == count


@pytest.mark.parametrize("stage", ["retrieval", "answer"])
def test_crash_after_admission_without_saved_response_is_conservative(
    config, model_channel, monkeypatch, stage
):
    class ProcessExit(BaseException):
        pass

    value, _ = model_channel
    calls = responding(monkeypatch)
    complete = model_gateway.complete

    def interrupted(*args):
        if (stage == "retrieval" and not calls) or (stage == "answer" and len(calls) == 1):
            calls.append(args[1])
            raise ProcessExit()
        return complete(*args)

    monkeypatch.setattr(model_gateway, "complete", interrupted)
    capture(config, value)
    with pytest.raises(ProcessExit):
        dispatch.advance(config, value["id"], value["revision"])
    count = len(calls)
    dispatch.advance(config, value["id"], value["revision"])
    assert result(config, value)[1]["error_code"] == "channel_model_result_unknown"
    assert len(calls) == count
    with database.connect(config.state_root) as db:
        assert (
            db.execute(
                "SELECT count(*) FROM model_configuration_calls WHERE state='running'"
            ).fetchone()[0]
            == 1
        )


def test_checkpoint_before_admission_can_continue_without_duplicate_request(
    config, model_channel, monkeypatch
):
    from fiber_bundle.channels import model_call

    value, _ = model_channel
    calls = responding(monkeypatch)
    capture(config, value)
    original = model_call.complete
    monkeypatch.setattr(
        model_call, "complete", lambda *a: (_ for _ in ()).throw(database.LockTimeout())
    )
    with pytest.raises(database.LockTimeout):
        dispatch.advance(config, value["id"], value["revision"])
    assert calls == []
    monkeypatch.setattr(model_call, "complete", original)
    dispatch.advance(config, value["id"], value["revision"])
    assert result(config, value)[1]["result"]["citation_integrity"] == "verified"
    assert len(calls) == 2


def test_source_change_after_saved_answer_prevents_cached_delivery(
    config, model_channel, monkeypatch
):
    value, root = model_channel
    calls = responding(monkeypatch)
    capture(config, value)
    interrupt_after(monkeypatch, "answer")
    with pytest.raises(database.LockTimeout):
        dispatch.advance(config, value["id"], value["revision"])
    (root / "a.md").write_text("# 青松\n此资料已由用户更改，不能交付旧的压力结论。")
    dispatch.advance(config, value["id"], value["revision"])
    _, data = result(config, value)
    assert data["error_code"] == "channel_source_changed"
    assert data["result"]["sources"] == [] and "2.4" not in data["result"]["answer"]
    assert len(calls) == 2


def test_model_change_after_saved_plan_prevents_next_request(config, model_channel, monkeypatch):
    value, _ = model_channel
    calls = responding(monkeypatch)
    capture(config, value)
    interrupt_after(monkeypatch, "retrieval")
    with pytest.raises(database.LockTimeout):
        dispatch.advance(config, value["id"], value["revision"])
    with database.connect(config.state_root) as db:
        db.execute("UPDATE model_configurations SET revision=revision+1")
    dispatch.advance(config, value["id"], value["revision"])
    assert result(config, value)[1]["error_code"] == "channel_model_policy_changed"
    assert len(calls) == 1


def test_revocation_during_interruption_blocks_recovery_without_calls(
    config, model_channel, monkeypatch
):
    from fiber_bundle import auth
    from fiber_bundle.channels.validation import ChannelValidationError

    value, _ = model_channel
    calls = responding(monkeypatch)
    capture(config, value)
    interrupt_after(monkeypatch, "retrieval")
    with pytest.raises(database.LockTimeout):
        dispatch.advance(config, value["id"], value["revision"])
    with database.connect(config.state_root) as db:
        auth.revoke_client(db, value["settings"]["client_id"])
    with pytest.raises((auth.Denied, ChannelValidationError)):
        dispatch.advance(config, value["id"], value["revision"])
    assert result(config, value)[0] == "processing" and len(calls) == 1


def test_concurrent_tick_does_not_treat_active_request_as_interrupted(
    config, model_channel, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    value, _ = model_channel
    calls = responding(monkeypatch)
    original = model_gateway.complete
    entered, release = Event(), Event()

    def held(*args):
        if not calls:
            entered.set()
            assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(model_gateway, "complete", held)
    capture(config, value)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(dispatch.advance, config, value["id"], value["revision"])
        assert entered.wait(5)
        try:
            dispatch.advance(config, value["id"], value["revision"])
            assert result(config, value)[0] == "processing" and calls == []
        finally:
            release.set()
        first.result(timeout=5)
    assert result(config, value)[1]["result"]["citation_integrity"] == "verified"
    assert len(calls) == 2


def test_saved_response_survives_actual_worker_process_exit(
    config, model_channel, monkeypatch, tmp_path
):
    import multiprocessing
    import os

    from fiber_bundle import model_budgets

    value, _ = model_channel
    record = tmp_path / "synthetic-provider-calls.json"
    record.write_text("[]")

    def provider(policy, identity, payload):
        calls = json.loads(record.read_text())
        calls.append(identity)
        record.write_text(json.dumps(calls))
        if len(calls) == 1:
            return envelope({"queries": ["青松"]})
        return envelope({"claims": [{"text": "压力为2.4 MPa。", "citations": ["[1]"]}]})

    monkeypatch.setattr(model_gateway, "complete", provider)
    capture(config, value)

    def interrupted_worker():
        # The durable response exists; the process dies before committing usage.
        monkeypatch.setattr(model_budgets, "settle", lambda *a: os._exit(75))
        dispatch.advance(config, value["id"], value["revision"])

    worker = multiprocessing.get_context("fork").Process(target=interrupted_worker)
    worker.start()
    try:
        worker.join(timeout=10)
        assert worker.exitcode == 75
    finally:
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=5)
    assert result(config, value)[0] == "processing"
    assert len(json.loads(record.read_text())) == 1
    dispatch.advance(config, value["id"], value["revision"])
    assert result(config, value)[1]["result"]["citation_integrity"] == "verified"
    calls = json.loads(record.read_text())
    assert len(calls) == len(set(calls)) == 2
    with database.connect(config.state_root) as db:
        assert model_budgets.inspect(db)[0]["used"] == 300


def test_changed_original_request_cannot_reuse_or_repeat_paid_plan(
    config, model_channel, monkeypatch
):
    from fiber_bundle.channels import store

    value, _ = model_channel
    calls = responding(monkeypatch)
    capture(config, value)
    interrupt_after(monkeypatch, "retrieval")
    with pytest.raises(database.LockTimeout):
        dispatch.advance(config, value["id"], value["revision"])
    state, data = result(config, value)
    data["question"] = "介绍其他资料"
    with store.opened(config) as db:
        db.execute("UPDATE events SET value=?", (json.dumps(data),))
    dispatch.advance(config, value["id"], value["revision"])
    assert result(config, value)[1]["error_code"] == "channel_model_result_unknown"
    assert len(calls) == 1


def test_zero_results_recovers_plan_without_answer_call(config, model_channel, monkeypatch):
    value, _ = model_channel
    calls = []
    monkeypatch.setattr(
        model_gateway,
        "complete",
        lambda *a: calls.append(a[1]) or envelope({"queries": ["无匹配资料"]}),
    )
    capture(config, value)
    interrupt_after(monkeypatch, "retrieval")
    with pytest.raises(database.LockTimeout):
        dispatch.advance(config, value["id"], value["revision"])
    dispatch.advance(config, value["id"], value["revision"])
    _, data = result(config, value)
    assert data["result"]["status"] == "insufficient_evidence"
    assert "未找到匹配资料" in data["result"]["answer"]
    assert len(calls) == 1


@pytest.mark.parametrize("damage", ["orphan", "failed"])
def test_success_cache_without_matching_live_ledger_never_admits_again(
    config, model_channel, monkeypatch, damage
):
    value, _ = model_channel
    calls = responding(monkeypatch)
    capture(config, value)
    interrupt_after(monkeypatch, "retrieval")
    with pytest.raises(database.LockTimeout):
        dispatch.advance(config, value["id"], value["revision"])
    with database.connect(config.state_root) as db:
        if damage == "orphan":
            db.execute("DELETE FROM model_configuration_calls")
        else:
            db.execute("UPDATE model_configuration_calls SET state='failed'")
    dispatch.advance(config, value["id"], value["revision"])
    assert result(config, value)[1]["error_code"] == "channel_model_result_unknown"
    assert len(calls) == 1
    with database.connect(config.state_root) as db:
        assert db.execute("SELECT count(*) FROM model_budget_reservations").fetchone()[0] == 1
