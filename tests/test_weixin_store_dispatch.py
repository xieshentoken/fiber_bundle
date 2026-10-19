"""Bounded Store scheduling, durable attempt recovery, and receiver lifecycle."""

import asyncio

import pytest
from test_weixin_batches import at, capture
from test_weixin_confirmations import prompt as prompt
from test_weixin_inbox import binding as binding
from test_weixin_inbox import poll
from test_weixin_receiver import Adapter, revoke
from test_weixin_store import main_rows

from fiber_bundle import auth
from fiber_bundle import weixin_batches as batches
from fiber_bundle import weixin_confirmations as confirmations
from fiber_bundle import weixin_receiver as receiver
from fiber_bundle import weixin_store as handoff
from fiber_bundle import weixin_store_dispatch as dispatch
from fiber_bundle.config import InvalidInput
from fiber_bundle.store import Store


def second_batch(config, binding):
    capture(config, binding, "later", 10)
    confirmations.process(config, binding)
    batch = batches.advance(config, binding, now=at(14))[0]["batch_id"]
    batches.choose(config, binding, batch, "save_one")
    return batch


@pytest.mark.parametrize("value", [-1, 101, True, "1", None])
def test_invalid_store_limits(value):
    with pytest.raises(InvalidInput):
        dispatch.limit(value)


def test_receiver_default_does_not_store(config, binding, prompt):
    batches.choose(config, binding, prompt[0], "save_one")
    adapter = Adapter()
    receiver.configure(adapter, config, binding)
    adapter._running = True

    async def fetch(*args, **kwargs):
        adapter._running = False
        return poll(cursor="received")

    asyncio.run(receiver.run(adapter, fetch))
    assert not adapter.errors and not main_rows(config, "receipts")


def test_budget_is_per_run_and_completed_batches_are_not_replayed(config, binding, prompt):
    batches.choose(config, binding, prompt[0], "save_one")
    second = second_batch(config, binding)

    def run():
        adapter = Adapter()
        receiver.configure(adapter, config, binding, store_limit=1)
        adapter._running = True
        polls = []

        async def fetch(*args, **kwargs):
            polls.append(1)
            if len(polls) == 3:
                adapter._running = False
            return poll(cursor=f"poll-{len(polls)}")

        asyncio.run(receiver.run(adapter, fetch))
        assert not adapter.errors

    run()
    assert len(main_rows(config, "receipts")) == 1
    run()
    assert len(main_rows(config, "receipts")) == 2
    run()
    assert len(main_rows(config, "receipts")) == 2
    assert [a["state"] for a in dispatch.attempts(config, binding, second)] == ["stored"]


def test_waiting_question_and_cancelled_batches_are_skipped(config, binding, prompt):
    batches.choose(config, binding, prompt[0], "consult")
    second = second_batch(config, binding)
    batches.choose(config, binding, second, "cancel")
    assert dispatch.drain(config, binding, maximum=10) == 0
    batches.choose(config, binding, prompt[0], "consult", question="请比较")
    assert dispatch.drain(config, binding, maximum=10) == 1
    assert main_rows(config, "source_versions")[0]["retention"] == "session"


@pytest.mark.parametrize("crash", [False, True])
def test_failure_or_crash_requires_explicit_resume_and_preserves_history(
    config, binding, prompt, monkeypatch, crash
):
    batches.choose(config, binding, prompt[0], "save_separately")
    second_batch(config, binding)
    original = Store.ingest_locked
    calls = []

    def fail(self, *args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            if crash:
                raise SystemExit("simulated process exit")
            raise OSError("synthetic failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Store, "ingest_locked", fail)
    with pytest.raises((OSError, SystemExit)):
        dispatch.drain(config, binding, maximum=10)
    assert len(main_rows(config, "receipts")) == 1
    state = "running" if crash else "failed"
    assert dispatch.attempts(config, binding, prompt[0])[0]["state"] == state
    with pytest.raises(InvalidInput, match="recovery"):
        dispatch.drain(config, binding, maximum=10)
    monkeypatch.setattr(Store, "ingest_locked", original)
    assert dispatch.resume(config, binding, prompt[0])["store_state"] == "stored"
    assert [a["state"] for a in dispatch.attempts(config, binding, prompt[0])] == ["stored", state]
    assert len(main_rows(config, "receipts")) == 2
    assert dispatch.drain(config, binding, maximum=10) == 1
    assert len(main_rows(config, "receipts")) == 3


def test_cancelled_partial_work_cannot_resume_but_does_not_block_other_batches(
    config, binding, prompt, monkeypatch
):
    batches.choose(config, binding, prompt[0], "save_one")
    original = Store.ingest_locked

    def fail(*args, **kwargs):
        raise OSError("synthetic")

    monkeypatch.setattr(Store, "ingest_locked", fail)
    with pytest.raises(OSError):
        dispatch.drain(config, binding, maximum=1)
    batches.choose(config, binding, prompt[0], "cancel")
    with pytest.raises(InvalidInput):
        dispatch.resume(config, binding, prompt[0])
    second_batch(config, binding)
    monkeypatch.setattr(Store, "ingest_locked", original)
    assert dispatch.drain(config, binding, maximum=1) == 1


def test_direct_partial_handoff_requires_resume_and_worker_lock(
    config, binding, prompt, monkeypatch
):
    batches.choose(config, binding, prompt[0], "save_one")
    with pytest.raises(InvalidInput, match="no interrupted"):
        dispatch.resume(config, binding, prompt[0])
    original = Store.ingest_locked

    def fail(*args, **kwargs):
        raise OSError("synthetic")

    monkeypatch.setattr(Store, "ingest_locked", fail)
    with pytest.raises(OSError):
        handoff.transfer(config, binding, prompt[0])
    monkeypatch.setattr(Store, "ingest_locked", original)
    with pytest.raises(InvalidInput, match="partial"):
        dispatch.drain(config, binding, maximum=1)
    with dispatch.worker_lock(config):
        with pytest.raises(InvalidInput, match="active"):
            dispatch.resume(config, binding, prompt[0])
    dispatch.resume(config, binding, prompt[0])
    assert dispatch.drain(config, binding, maximum=1) == 0


def test_revoked_cannot_dispatch(config, binding, prompt):
    batches.choose(config, binding, prompt[0], "save_one")
    revoke(config, binding)
    with pytest.raises(auth.Denied):
        dispatch.drain(config, binding, maximum=1)
    assert not main_rows(config, "receipts")


def test_receiver_store_failure_stops_without_fetch(config, binding, prompt, monkeypatch):
    batches.choose(config, binding, prompt[0], "save_one")

    def fail(*args, **kwargs):
        raise OSError("private details")

    monkeypatch.setattr(Store, "ingest_locked", fail)
    adapter = Adapter()
    receiver.configure(adapter, config, binding, store_limit=1)
    adapter._running = True

    async def forbidden(*args, **kwargs):
        pytest.fail("failed store must stop loop")

    asyncio.run(receiver.run(adapter, forbidden))
    assert adapter.errors[0][0] == "fiber_weixin_store_failed"
    assert "private" not in str(adapter.errors)


def test_receiver_cancellation_stops_between_batches(config, binding, prompt, monkeypatch):
    batches.choose(config, binding, prompt[0], "save_one")
    second_batch(config, binding)
    original = Store.ingest_locked

    def cancel_after_commit(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        asyncio.current_task().cancel()
        return result

    monkeypatch.setattr(Store, "ingest_locked", cancel_after_commit)
    adapter = Adapter()
    receiver.configure(adapter, config, binding, store_limit=2)
    adapter._running = True

    async def forbidden(*args, **kwargs):
        pytest.fail("cancelled receiver must not fetch")

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(receiver.run(adapter, forbidden))
    assert len(main_rows(config, "receipts")) == 1
    assert adapter.disconnected == 1 and not adapter.errors


def test_polled_command_is_committed_before_store_handoff(config, binding, prompt):
    from test_weixin_inbox import message

    from fiber_bundle import weixin_inbox as inbox

    adapter = Adapter()
    receiver.configure(adapter, config, binding, store_limit=1)
    adapter._running = True
    polls = []

    async def fetch(*args, **kwargs):
        polls.append(1)
        if len(polls) == 1:
            assert not main_rows(config, "receipts")
            return poll(
                message(
                    "save-command",
                    item_list=[
                        {
                            "type": 1,
                            "text_item": {"text": f"批次 {prompt[1]['prompt_id']} 保存为一份"},
                        }
                    ],
                ),
                cursor="command-committed",
            )
        assert inbox.cursor(config, binding) == "command-committed"
        assert handoff.get(config, binding, prompt[0])["store_state"] == "stored"
        adapter._running = False
        return poll(cursor="final")

    asyncio.run(receiver.run(adapter, fetch))
    assert not adapter.errors and len(main_rows(config, "receipts")) == 1
