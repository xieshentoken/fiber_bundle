"""Bounded receiver delivery, durable failure stops and captured-response preservation."""

import asyncio

import pytest
from test_weixin_confirmations import command
from test_weixin_confirmations import prompt as prompt
from test_weixin_inbox import binding as binding
from test_weixin_inbox import message, poll, rows
from test_weixin_receiver import Adapter, revoke

from fiber_bundle import weixin_confirmations as confirmations
from fiber_bundle import weixin_inbox as inbox
from fiber_bundle import weixin_outbound_dispatch as dispatch
from fiber_bundle import weixin_receipt_delivery as receipts
from fiber_bundle import weixin_receiver as receiver
from fiber_bundle.config import InvalidInput


@pytest.mark.parametrize("value", [-1, 101, True, 1.5, "1", None])
def test_invalid_budgets(value):
    with pytest.raises(InvalidInput):
        dispatch.limit(value)


def test_default_disabled_and_missing_sender_rejected(config, binding, prompt):
    adapter = Adapter()
    with pytest.raises(InvalidInput, match="sender"):
        receiver.configure(adapter, config, binding, outbound_limit=1)
    receiver.configure(adapter, config, binding)
    adapter._running = True

    async def fetch(*args, **kwargs):
        adapter._running = False
        return poll(cursor="disabled")

    asyncio.run(receiver.run(adapter, fetch))
    assert not adapter.errors
    assert confirmations.list_prompts(config, binding)["prompts"][0]["state"] == "pending"


def test_receipts_first_and_stale_prompt_skipped(config, binding, prompt):
    job = command(config, binding, f"批次 {prompt[1]['prompt_id']} 取消")
    confirmations.process(config, binding)
    calls = []

    async def send(owner, text):
        calls.append(text)
        return True

    assert asyncio.run(dispatch.drain(config, binding, send, maximum=10)) == 1
    assert "操作回执" in calls[0]
    assert receipts.prepare(config, binding, job)["state"] == "sent"
    assert confirmations.list_prompts(config, binding)["prompts"][0]["state"] == "pending"
    assert asyncio.run(dispatch.drain(config, binding, send, maximum=10)) == 0


def test_per_run_budget_not_per_poll_and_restart_only_sends_unsent(config, binding, prompt):
    command(config, binding, "批次 invalid 无效一", "one-command")
    command(config, binding, "批次 invalid 无效二", "two-command")
    calls = []

    async def send(owner, text):
        calls.append(text)
        return True

    def run_once():
        adapter = Adapter()
        adapter._fiber_send_once = send
        receiver.configure(adapter, config, binding, outbound_limit=1)
        adapter._running = True
        fetched = []

        async def fetch(*args, **kwargs):
            fetched.append(1)
            if len(fetched) == 3:
                adapter._running = False
            return poll(cursor=f"page-{len(calls)}-{len(fetched)}")

        asyncio.run(receiver.run(adapter, fetch))
        assert not adapter.errors and len(fetched) == 3

    run_once()
    assert len(calls) == 1
    run_once()
    assert len(calls) == 2 and calls[0] != calls[1]
    run_once()
    assert len(calls) == 3 and "请复制" in calls[-1]
    run_once()
    assert len(calls) == 3


@pytest.mark.parametrize("outcome", [False, "exception"])
def test_unknown_stops_before_next_fetch_and_restart_remains_blocked(
    config, binding, prompt, outcome
):
    calls = []

    async def send(*args):
        calls.append(1)
        if outcome == "exception":
            raise OSError("private transport detail")
        return False

    async def forbidden(*args, **kwargs):
        pytest.fail("unknown delivery must stop polling")

    for _ in range(2):
        adapter = Adapter()
        adapter._fiber_send_once = send
        receiver.configure(adapter, config, binding, outbound_limit=1)
        adapter._running = True
        asyncio.run(receiver.run(adapter, forbidden))
        assert adapter.errors[0][0] == "fiber_weixin_outbound_failed"
        assert "private" not in str(adapter.errors)
        assert adapter.disconnected == 1
    assert len(calls) == 1


def test_response_commits_before_failed_receipt_send(config, binding):
    calls = []
    adapter = Adapter()

    async def send(*args):
        calls.append(1)
        assert inbox.cursor(config, binding) == "captured"
        assert rows(config, "wx_command_receipts")[0]["kind"] == "command"
        return False

    adapter._fiber_send_once = send
    receiver.configure(adapter, config, binding, outbound_limit=1)
    adapter._running = True

    async def fetch(*args, **kwargs):
        assert not calls
        return poll(
            message(item_list=[{"type": 1, "text_item": {"text": "批次 invalid 取消"}}]),
            cursor="captured",
        )

    asyncio.run(receiver.run(adapter, fetch))
    assert len(calls) == 1 and len(rows(config, "events")) == 1
    assert inbox.cursor(config, binding) == "captured"
    assert adapter.errors[0][0] == "fiber_weixin_outbound_failed"


def test_cancelled_send_is_not_replayed_after_restart(config, binding, prompt):
    calls = []
    adapter = Adapter()

    async def scenario():
        entered = asyncio.Event()

        async def send(*args):
            calls.append(1)
            entered.set()
            await asyncio.Future()

        adapter._fiber_send_once = send
        receiver.configure(adapter, config, binding, outbound_limit=1)
        adapter._running = True

        async def forbidden(*args, **kwargs):
            pytest.fail("must not fetch")

        task = asyncio.create_task(receiver.run(adapter, forbidden))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        receiver.configure(adapter, config, binding, outbound_limit=1)
        adapter._running = True
        await receiver.run(adapter, forbidden)

    asyncio.run(scenario())
    assert len(calls) == 1
    assert confirmations.list_prompts(config, binding)["prompts"][0]["state"] == "sending"
    assert adapter.errors[0][0] == "fiber_weixin_outbound_failed"


def test_revocation_prevents_outbound_and_poll(config, binding, prompt):
    adapter = Adapter()

    async def forbidden(*args, **kwargs):
        pytest.fail("revoked callback")

    adapter._fiber_send_once = forbidden
    receiver.configure(adapter, config, binding, outbound_limit=1)
    revoke(config, binding)
    adapter._running = True
    asyncio.run(receiver.run(adapter, forbidden))
    assert adapter.errors[0][0] == "fiber_weixin_preflight_failed"


def test_silence_prompt_waits_for_poll_commit_before_delivery(config, binding, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from fiber_bundle import weixin_batches as batches

    adapter = Adapter()
    calls = []
    polls = []
    advance = batches.advance

    def timed_advance(config, binding, *, quiet_seconds):
        events = rows(config, "events")
        current = datetime.fromisoformat(events[0]["received_at"]) if events else datetime.now(UTC)
        if len(polls) >= 2:
            current += timedelta(seconds=4)
        return advance(config, binding, quiet_seconds=quiet_seconds, now=current)

    monkeypatch.setattr(batches, "advance", timed_advance)

    async def send(owner, text):
        assert inbox.cursor(config, binding) == "second"
        calls.append(text)
        return True

    adapter._fiber_send_once = send
    receiver.configure(adapter, config, binding, quiet_seconds=0.02, outbound_limit=1)
    adapter._running = True

    async def fetch(*args, **kwargs):
        polls.append(1)
        if len(polls) == 1:
            return poll(message(), cursor="first")
        if len(polls) == 2:
            for _ in range(100):
                if confirmations.list_prompts(config, binding)["prompts"]:
                    break
                await asyncio.sleep(0.01)
            assert not calls
            assert confirmations.list_prompts(config, binding)["prompts"][0]["state"] == "pending"
            return poll(cursor="second")
        assert len(calls) == 1
        adapter._running = False
        return poll(cursor="third")

    asyncio.run(receiver.run(adapter, fetch))
    assert not adapter.errors and len(calls) == 1
    assert inbox.cursor(config, binding) == "third"
