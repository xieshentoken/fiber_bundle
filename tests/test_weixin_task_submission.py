"""Real isolated queue transactions and receiver integration; no network/model execution."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest
from test_execution import policy as policy
from test_weixin_batches import at, capture
from test_weixin_inbox import binding as binding
from test_weixin_inbox import message, poll, rows
from test_weixin_receiver import Adapter, revoke
from test_weixin_store import main_rows
from test_weixin_task_inputs import CAPTURE, query_grant

from fiber_bundle import auth, database, execution_ledger
from fiber_bundle import weixin_batches as batches
from fiber_bundle import weixin_confirmations as confirmations
from fiber_bundle import weixin_inbox as inbox
from fiber_bundle import weixin_receiver as receiver
from fiber_bundle import weixin_store as handoff
from fiber_bundle import weixin_task_bindings as bindings
from fiber_bundle import weixin_task_submission as submission
from fiber_bundle.config import IntegrityError, InvalidInput


def batch(config, binding, texts, action="save_one", *, transfer=True):
    confirmations.initialize(config, binding)
    offset = 20 * (len(rows(config, "events")) + 1)
    for i, text in enumerate(texts):
        capture(
            config,
            binding,
            f"event-{offset}-{i}",
            offset + i,
            item_list=[{"type": 1, "text_item": {"text": text}}],
            to_user_id=binding.account_id,
            from_user_id=binding.owner_id,
        )
    confirmations.process(config, binding)
    bid = batches.advance(config, binding, now=at(offset + len(texts) + 3))[0]["batch_id"]
    if action:
        batches.choose(
            config, binding, bid, action, question="合成正文" if action == "consult" else None
        )
    if transfer:
        handoff.transfer(config, binding, bid)
    return bid


def dispatch(config, binding, policy, maximum=10):
    return submission.dispatch(config, binding, policy, maximum=maximum, capture_policy=CAPTURE)


def no_effects(config):
    assert not main_rows(config, "model_calls")
    assert all(
        r["state"] == "queued" and r["attempts"] == 0 for r in main_rows(config, "execution_runs")
    )


@pytest.mark.parametrize("value", [-1, 101, True, 1.0, "1", None])
def test_invalid_limit(value):
    with pytest.raises(InvalidInput):
        submission.limit(value)


def test_default_off_and_missing_policy(config, binding, policy):
    batch(config, binding, ["https://x.com/a/status/1"])
    assert submission.dispatch(config, binding, policy)["state"] == "disabled"
    adapter = Adapter()
    receiver.configure(adapter, config, binding)
    assert adapter._fiber_submission_limit == 0
    with pytest.raises(InvalidInput, match="explicit execution policy"):
        receiver.configure(adapter, config, binding, submission_limit=1)
    assert not main_rows(config, "execution_runs")


def test_task_budget_separate_parents_and_idempotent_replay(config, binding, policy):
    bid = batch(
        config,
        binding,
        ["https://x.com/a/status/1", "https://mp.weixin.qq.com/s/2"],
        "save_separately",
    )
    first = dispatch(config, binding, policy, 1)
    assert first["processed"] == 1 and first["state"] == "limit_reached"
    second = dispatch(config, binding, policy, 1)
    assert second["processed"] == 1
    assert first["results"][0]["run_id"] != second["results"][0]["run_id"]
    assert dispatch(config, binding, policy)["state"] == "idle"
    assert len(main_rows(config, "execution_runs")) == 2
    assert {r["batch_id"] for r in first["results"] + second["results"]} == {bid}
    assert len(rows(config, "wx_task_bindings")) == 2
    no_effects(config)


def test_consultation_one_extractive_task_without_capture_policy(config, binding, policy):
    query_grant(config, binding)
    batch(config, binding, ["合成正文第一份", "合成正文第二份"], "consult")
    result = submission.dispatch(config, binding, policy, maximum=3)
    assert result["processed"] == 1 and result["state"] == "idle"
    assert result["results"][0]["kind"] == "answer"
    assert len(main_rows(config, "answer_tasks")) == 1
    assert {v["retention"] for v in main_rows(config, "source_versions")} == {"session"}
    no_effects(config)


@pytest.mark.parametrize(
    "texts",
    [
        ["普通资料"],
        ["正文 https://x.com/a/status/1"],
        ["https://x.com/a", "https://x.com/b"],
        ["https://unsupported.invalid/a"],
    ],
)
def test_unsupported_or_ambiguous_pending_without_derived_receipt(config, binding, policy, texts):
    bid = batch(config, binding, texts)
    result = dispatch(config, binding, policy)
    assert result["state"] == "pending" and result["processed"] == 0
    assert result["pending"][0]["batch_id"] == bid
    assert result["pending"][0]["reason"] == "article_input_requires_clarification"
    assert len(main_rows(config, "receipts")) == 1
    assert not main_rows(config, "execution_runs")


def test_pending_does_not_block_later_valid_batch(config, binding, policy):
    batch(config, binding, ["资料"])
    bid = batch(config, binding, ["https://x.com/a/status/2"])
    result = dispatch(config, binding, policy)
    assert result["processed"] == 1 and result["state"] == "pending"
    assert result["results"][0]["batch_id"] == bid
    assert len(result["pending"]) == 1
    no_effects(config)


def test_missing_capture_is_explicit_pending(config, binding, policy):
    batch(config, binding, ["https://x.com/a/status/1"])
    result = submission.dispatch(config, binding, policy, maximum=1)
    assert result["pending"][0]["reason"] == "capture_policy_required"
    assert not main_rows(config, "execution_runs")


@pytest.mark.parametrize("committed", [False, True])
def test_crash_before_or_after_main_commit_recovers_same_run(
    config, binding, policy, monkeypatch, committed
):
    batch(config, binding, ["https://x.com/a/status/1"])
    original = execution_ledger.create_run

    def crash(*args, **kwargs):
        if committed:
            original(*args, **kwargs)
        raise SystemExit("synthetic crash")

    monkeypatch.setattr(execution_ledger, "create_run", crash)
    with pytest.raises(SystemExit):
        dispatch(config, binding, policy)
    intended = rows(config, "wx_task_bindings")[0]["run_id"]
    monkeypatch.setattr(execution_ledger, "create_run", original)
    assert dispatch(config, binding, policy)["processed"] == int(not committed)
    assert main_rows(config, "execution_runs")[0]["id"] == intended
    assert len(main_rows(config, "execution_runs")) == 1
    no_effects(config)


def test_partial_batch_failure_keeps_first_and_recovers_second(
    config, binding, policy, monkeypatch
):
    batch(config, binding, ["https://x.com/a", "https://x.com/b"], "save_separately")
    original = execution_ledger.create_run
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("synthetic failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(execution_ledger, "create_run", fail)
    with pytest.raises(OSError):
        dispatch(config, binding, policy)
    intended = [r["run_id"] for r in rows(config, "wx_task_bindings")]
    assert len(main_rows(config, "execution_runs")) == 1
    monkeypatch.setattr(execution_ledger, "create_run", original)
    assert dispatch(config, binding, policy)["processed"] == 1
    assert {r["id"] for r in main_rows(config, "execution_runs")} == set(intended)
    no_effects(config)


def test_parallel_workers_reject_overlap_then_replay_is_idle(config, binding, policy, monkeypatch):
    batch(config, binding, ["https://x.com/a"])
    original = bindings.submit
    entered, release = Event(), Event()

    def held(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(bindings, "submit", held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(dispatch, config, binding, policy)
        assert entered.wait(5)
        try:
            with pytest.raises(InvalidInput, match="already active"):
                dispatch(config, binding, policy)
        finally:
            release.set()
        assert future.result()["processed"] == 1
    assert dispatch(config, binding, policy)["processed"] == 0
    no_effects(config)


def test_cancel_frozen_unsaved_and_missing_media_do_not_submit(config, binding, policy):
    cancelled = batch(config, binding, ["https://x.com/a"])
    batches.choose(config, binding, cancelled, "cancel")
    batch(config, binding, ["https://x.com/b"], action=None, transfer=False)
    batch(config, binding, ["https://x.com/c"], transfer=False)
    capture(config, binding, "image", 1000, item_list=[{"type": 2, "image_item": {}}])
    confirmations.process(config, binding)
    incomplete = batches.advance(config, binding, now=at(1004))[0]["batch_id"]
    with pytest.raises(InvalidInput, match="incomplete"):
        batches.choose(config, binding, incomplete, "save_one")
    assert dispatch(config, binding, policy)["processed"] == 0
    assert not main_rows(config, "execution_runs")


@pytest.mark.parametrize("change", ["cancel", "revoke"])
def test_gate_rechecked_between_discovery_and_submit(config, binding, policy, monkeypatch, change):
    bid = batch(config, binding, ["https://x.com/a"])
    original = bindings.submit

    def invalidate(*args, **kwargs):
        if change == "cancel":
            batches.choose(config, binding, bid, "cancel")
        else:
            revoke(config, binding)
        return original(*args, **kwargs)

    monkeypatch.setattr(bindings, "submit", invalidate)
    with pytest.raises((auth.Denied, InvalidInput)):
        dispatch(config, binding, policy)
    assert not main_rows(config, "execution_runs")


def test_query_revocation_is_error_not_idle(config, binding, policy):
    query_grant(config, binding)
    batch(config, binding, ["合成正文"], "consult")
    db = database.connect(config.state_root)
    auth.set_grant(db, binding.client_id, binding.vault_id, add=True, query=False, original=False)
    db.close()
    with pytest.raises(auth.Denied):
        dispatch(config, binding, policy)
    assert not main_rows(config, "execution_runs")


def test_ledger_corruption_propagates(config, binding, policy):
    batch(config, binding, ["https://x.com/a"])
    with inbox.connection(config, binding) as db:
        db.execute("UPDATE wx_store_handoffs SET plan_json='{}'")
    with pytest.raises(IntegrityError):
        dispatch(config, binding, policy)
    assert not main_rows(config, "execution_runs")


def test_missing_native_task_is_error_with_full_permissions(config, binding, policy):
    bid = batch(config, binding, ["https://x.com/a"])
    bindings.submit(config, binding, bid, policy, capture_policy=CAPTURE)
    db = database.connect(config.state_root)
    auth.set_grant(db, binding.client_id, binding.vault_id, add=True, query=True, original=True)
    db.execute("DELETE FROM article_tasks")
    db.commit()
    db.close()
    with pytest.raises(InvalidInput, match="task conflicts"):
        dispatch(config, binding, policy)
    assert len(main_rows(config, "execution_runs")) == 1


@pytest.mark.parametrize("capture_change", [False, True])
def test_changed_policy_does_not_replace_existing_run(config, binding, policy, capture_change):
    bid = batch(config, binding, ["https://x.com/a"])
    bindings.submit(config, binding, bid, policy, capture_policy=CAPTURE)
    with pytest.raises(InvalidInput, match="conflicts"):
        submission.dispatch(
            config,
            binding,
            policy if capture_change else replace(policy, max_attempts=3),
            maximum=1,
            capture_policy=replace(CAPTURE, timeout_seconds=61) if capture_change else CAPTURE,
        )
    assert len(main_rows(config, "execution_runs")) == 1


def test_account_scope_and_existing_interrupted_run_not_restarted(config, binding, policy):
    other = replace(binding, account_id="other-account")
    inbox.initialize(config, other)
    batch(config, other, ["https://x.com/other"])
    bid = batch(config, binding, ["https://x.com/a"])
    run = bindings.submit(config, binding, bid, policy, capture_policy=CAPTURE)["run_id"]
    db = database.connect(config.state_root)
    db.execute("UPDATE execution_runs SET state='interrupted' WHERE id=?", (run,))
    db.commit()
    db.close()
    assert dispatch(config, binding, policy)["processed"] == 0
    assert len(main_rows(config, "execution_runs")) == 1
    assert main_rows(config, "execution_runs")[0]["state"] == "interrupted"


def test_receiver_budget_per_run_default_off_then_restart_progress(config, binding, policy):
    batch(config, binding, ["https://x.com/a", "https://x.com/b"], "save_separately")

    def run(enabled):
        adapter = Adapter()
        receiver.configure(
            adapter,
            config,
            binding,
            submission_limit=int(enabled),
            execution_policy=policy,
            capture_policy=CAPTURE,
        )
        adapter._running = True
        polls = []

        async def fetch(*args, **kwargs):
            polls.append(1)
            if len(polls) == 3:
                adapter._running = False
            return poll(cursor=f"poll-{len(polls)}")

        asyncio.run(receiver.run(adapter, fetch))
        assert not adapter.errors

    run(False)
    assert not main_rows(config, "execution_runs")
    run(True)
    assert len(main_rows(config, "execution_runs")) == 1
    run(True)
    assert len(main_rows(config, "execution_runs")) == 2
    run(True)
    assert len(main_rows(config, "execution_runs")) == 2
    no_effects(config)


def test_receiver_failure_stops_without_fetch_or_private_error(
    config, binding, policy, monkeypatch
):
    batch(config, binding, ["https://x.com/a"])

    def fail(*args, **kwargs):
        raise OSError("private body")

    monkeypatch.setattr(bindings, "submit", fail)
    adapter = Adapter()
    receiver.configure(
        adapter,
        config,
        binding,
        submission_limit=1,
        execution_policy=policy,
        capture_policy=CAPTURE,
    )
    adapter._running = True

    async def forbidden(*args, **kwargs):
        pytest.fail("failed submission cannot fetch")

    asyncio.run(receiver.run(adapter, forbidden))
    assert adapter.errors[0][0] == "fiber_weixin_submission_failed"
    assert "private" not in str(adapter.errors)


def test_receiver_cancellation_between_tasks(config, binding, policy, monkeypatch):
    batch(config, binding, ["https://x.com/a", "https://x.com/b"], "save_separately")
    original = bindings.submit

    def cancel(*args, **kwargs):
        result = original(*args, **kwargs)
        asyncio.current_task().cancel()
        return result

    monkeypatch.setattr(bindings, "submit", cancel)
    adapter = Adapter()
    receiver.configure(
        adapter,
        config,
        binding,
        submission_limit=2,
        execution_policy=policy,
        capture_policy=CAPTURE,
    )
    adapter._running = True

    async def forbidden(*args, **kwargs):
        pytest.fail("cancelled receiver cannot fetch")

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(receiver.run(adapter, forbidden))
    assert len(main_rows(config, "execution_runs")) == 1 and adapter.disconnected == 1
    no_effects(config)


def test_polled_confirmation_commits_before_store_and_submission(config, binding, policy):
    bid = batch(config, binding, ["https://x.com/a"], action=None, transfer=False)
    prompt = confirmations.prepare(config, binding, bid)
    adapter = Adapter()
    receiver.configure(
        adapter,
        config,
        binding,
        store_limit=1,
        submission_limit=1,
        execution_policy=policy,
        capture_policy=CAPTURE,
    )
    adapter._running = True
    polls = []

    async def fetch(*args, **kwargs):
        polls.append(1)
        if len(polls) == 1:
            assert not main_rows(config, "receipts")
            assert not main_rows(config, "execution_runs")
            return poll(
                message(
                    "confirmation",
                    item_list=[
                        {"type": 1, "text_item": {"text": f"批次 {prompt['prompt_id']} 保存为一份"}}
                    ],
                ),
                cursor="confirmed",
            )
        assert inbox.cursor(config, binding) == "confirmed"
        assert handoff.get(config, binding, bid)["store_state"] == "stored"
        assert len(main_rows(config, "execution_runs")) == 1
        assert rows(config, "wx_task_bindings")[0]["batch_id"] == bid
        adapter._running = False
        return poll(cursor="final")

    asyncio.run(receiver.run(adapter, fetch))
    assert len(polls) == 2 and not adapter.errors
    no_effects(config)
