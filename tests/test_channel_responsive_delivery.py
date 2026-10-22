"""Slow filesystem/security checks must not suppress the receiver heartbeat."""

import asyncio
import threading
import time

from test_message_channels import bind
from test_message_channels import channel as channel
from test_weixin_expert_output import Session, guarded, runtime

from fiber_bundle.channels import dispatch, weixin_output, weixin_poll


def test_slow_send_authorization_keeps_event_loop_responsive():
    adapter, module = runtime(Session())
    guard, receipts = guarded()
    loop_thread = threading.get_ident()
    threads = []

    def check():
        threads.append(threading.get_ident())
        time.sleep(0.04)

    guard.recheck = check

    async def exercise():
        ticks = 0
        task = asyncio.create_task(
            weixin_output.send(
                adapter, module, "owner", "answer", "context", guard, lambda _: check()
            )
        )
        while not task.done():
            ticks += 1
            await asyncio.sleep(0.005)
        assert await task
        return ticks

    assert asyncio.run(exercise()) >= 8
    assert threads and all(t != loop_thread for t in threads)
    assert receipts["1"]["state"] == "sent"
    assert len(adapter._send_session.requests) == 1


def test_slow_receive_scope_check_does_not_block_other_tasks():
    adapter, module = runtime(Session())

    async def updates(*args, **kwargs):
        return {"msgs": []}

    module._get_updates = updates
    adapter._poll_session = None
    threads = []

    def check():
        threads.append(threading.get_ident())
        time.sleep(0.1)

    async def exercise():
        task = asyncio.create_task(weixin_poll.receive(adapter, module, "cursor", check))
        await asyncio.sleep(0.03)
        assert not task.done()
        assert await task == {"msgs": []}

    asyncio.run(exercise())
    assert threads != [threading.get_ident()]


def test_delivery_preparation_keeps_checks_and_source_changes_in_background(
    config, channel, monkeypatch
):
    value, _ = channel
    bind(config, value)
    assert dispatch.capture(config, value["id"], 1, "responsive", "ou_synthetic", "查询：压力")
    dispatch.advance(config, value["id"], 1)
    original = dispatch.query.recheck
    threads = []

    def check(*args):
        threads.append(threading.get_ident())
        time.sleep(0.1)
        return original(*args)

    monkeypatch.setattr(dispatch.query, "recheck", check)

    async def exercise():
        sent = []

        async def sender(owner, body, context, guard):
            await asyncio.to_thread(guard)
            sent.append(body)
            return True

        task = asyncio.create_task(dispatch.deliver(config, value["id"], 1, sender))
        await asyncio.sleep(0.03)
        assert not task.done()
        assert await task == "sent"
        assert len(sent) == 1

    asyncio.run(exercise())
    assert len(threads) >= 2 and all(t != threading.get_ident() for t in threads)
