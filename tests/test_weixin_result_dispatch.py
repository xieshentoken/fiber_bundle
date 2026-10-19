"""Opt-in result receipts share the receiver's one bounded outbound budget."""

import asyncio
from unittest.mock import patch

import pytest
from test_answer_tasks import invoke
from test_execution import policy as policy
from test_weixin_batches import at, capture
from test_weixin_confirmations import command
from test_weixin_inbox import binding as binding
from test_weixin_inbox import poll, rows
from test_weixin_receiver import Adapter
from test_weixin_task_inputs import query_grant

from fiber_bundle import auth, database
from fiber_bundle import execution_ledger as ledger
from fiber_bundle import weixin_batches as batches
from fiber_bundle import weixin_confirmations as confirmations
from fiber_bundle import weixin_inbox as inbox
from fiber_bundle import weixin_outbound_dispatch as outbound
from fiber_bundle import weixin_receiver as receiver
from fiber_bundle import weixin_result_queue as queue
from fiber_bundle import weixin_store as handoff
from fiber_bundle import weixin_task_bindings as bindings
from fiber_bundle import weixin_task_delivery as delivery
from fiber_bundle.config import InvalidInput


def task(config, binding, policy, index=0, *, complete=True):
    query_grant(config, binding)
    confirmations.initialize(config, binding)
    capture(
        config,
        binding,
        f"source-{index}",
        index * 10,
        item_list=[{"type": 1, "text_item": {"text": "合成正文"}}],
    )
    confirmations.process(config, binding)
    bid = batches.advance(config, binding, now=at(index * 10 + 4))[0]["batch_id"]
    batches.choose(config, binding, bid, "consult", question="合成正文")
    handoff.transfer(config, binding, bid)
    run_id = bindings.submit(config, binding, bid, policy)["run_id"]
    if complete:
        db = database.connect(config.state_root)
        ledger.start(config, db, run_id, policy)
        invoke(config, policy, run_id)
        ledger.transition(db, run_id, "succeeded")
        db.close()
    return bid, run_id


def senders(calls, *, acknowledged=True):
    async def normal(owner, text):
        calls.append(("normal", text))
        return True

    async def result(owner, text, guard):
        guard()
        calls.append(("result", text))
        return acknowledged

    return normal, result


def configured(config, binding, calls, *, maximum=1, enabled=True, acknowledged=True):
    adapter = Adapter()
    adapter._fiber_send_once, adapter._fiber_send_result_once = senders(
        calls, acknowledged=acknowledged
    )
    receiver.configure(adapter, config, binding, outbound_limit=maximum, result_receipts=enabled)
    adapter._running = True
    return adapter


def test_default_does_not_discover_or_send_results(config, binding, policy):
    bid, _ = task(config, binding, policy)
    calls = []
    adapter = configured(config, binding, calls, enabled=False)

    async def fetch(*args, **kwargs):
        adapter._running = False
        return poll(cursor="default-off")

    asyncio.run(receiver.run(adapter, fetch))
    assert not adapter.errors and calls == []
    assert delivery.prepare(config, binding, bid)["state"] == "pending"


@pytest.mark.parametrize("value", [None, 1, "yes"])
def test_result_switch_is_strict_boolean(config, binding, value):
    adapter = Adapter()
    with pytest.raises(InvalidInput, match="boolean"):
        receiver.configure(adapter, config, binding, result_receipts=value)


def test_enable_requires_positive_shared_budget_and_guarded_sender(config, binding):
    adapter = Adapter()
    adapter._fiber_send_once = senders([])[0]
    with pytest.raises(InvalidInput, match="budget and guarded sender"):
        receiver.configure(adapter, config, binding, result_receipts=True)
    with pytest.raises(InvalidInput, match="budget and guarded sender"):
        receiver.configure(adapter, config, binding, outbound_limit=1, result_receipts=True)


def test_shared_budget_commands_then_prompts_then_results(config, binding, policy):
    bid, _ = task(config, binding, policy)
    command(config, binding, "批次 invalid 无效命令")
    confirmations.process(config, binding)
    capture(config, binding, "waiting", 20)
    confirmations.process(config, binding)
    pending = batches.advance(config, binding, now=at(24))[0]["batch_id"]
    confirmations.prepare(config, binding, pending)
    calls = []
    normal, result = senders(calls)
    assert (
        asyncio.run(outbound.drain(config, binding, normal, maximum=2, result_sender=result)) == 2
    )
    assert all(kind == "normal" for kind, _ in calls)
    assert "操作回执" in calls[0][1] and "请复制" in calls[1][1]
    assert delivery.prepare(config, binding, bid)["state"] == "pending"
    assert (
        asyncio.run(outbound.drain(config, binding, normal, maximum=1, result_sender=result)) == 1
    )
    assert calls[-1][0] == "result"
    assert (
        asyncio.run(outbound.drain(config, binding, normal, maximum=10, result_sender=result)) == 0
    )


def test_per_run_budget_survives_multiple_polls_and_restart(config, binding, policy):
    first, _ = task(config, binding, policy)
    second, _ = task(config, binding, policy, 1)
    calls = []

    def run_once():
        adapter = configured(config, binding, calls)
        polls = []

        async def fetch(*args, **kwargs):
            polls.append(1)
            if len(polls) == 3:
                adapter._running = False
            return poll(cursor=f"round-{len(calls)}-{len(polls)}")

        asyncio.run(receiver.run(adapter, fetch))
        assert not adapter.errors and len(polls) == 3

    run_once()
    assert len(calls) == 1 and first in calls[0][1]
    run_once()
    assert len(calls) == 2 and second in calls[1][1]
    run_once()
    assert len(calls) == 2


def test_pending_skipped_but_revocation_stops(config, binding, policy):
    first, _ = task(config, binding, policy, complete=False)
    second, _ = task(config, binding, policy, 1)
    assert queue.next_batch(config, binding) == second
    db = database.connect(config.state_root)
    auth.set_grant(db, binding.client_id, binding.vault_id, add=True, query=False, original=False)
    db.close()
    with pytest.raises(auth.Denied):
        queue.next_batch(config, binding)
    assert first != second


def test_cancelled_ready_batch_is_skipped(config, binding, policy):
    first, _ = task(config, binding, policy)
    batches.choose(config, binding, first, "cancel")
    assert queue.next_batch(config, binding) is None


def test_unknown_stops_receiver_and_restart_before_fetch(config, binding, policy):
    task(config, binding, policy)
    calls = []

    async def forbidden(*args, **kwargs):
        pytest.fail("unresolved outbound must stop before poll")

    for _ in range(2):
        adapter = configured(config, binding, calls, acknowledged=False)
        asyncio.run(receiver.run(adapter, forbidden))
        assert adapter.errors[0][0] == "fiber_weixin_outbound_failed"
    assert len(calls) == 1
    assert rows(config, "wx_task_deliveries")[0]["state"] == "unknown"


def test_returned_poll_committed_before_result_send_failure(config, binding, policy):
    bid, run_id = task(config, binding, policy, complete=False)
    calls = []
    adapter = configured(config, binding, calls, acknowledged=False)

    async def fetch(*args, **kwargs):
        db = database.connect(config.state_root)
        ledger.transition(db, run_id, "failed")
        db.close()
        return poll(cursor="captured-before-failure")

    asyncio.run(receiver.run(adapter, fetch))
    assert inbox.cursor(config, binding) == "captured-before-failure"
    assert adapter.errors[0][0] == "fiber_weixin_outbound_failed"
    assert len(calls) == 1 and bid in calls[0][1]


def test_cancellation_at_sender_gate_still_blocks_receipt(config, binding, policy):
    bid, _ = task(config, binding, policy)
    calls = []
    normal, _ = senders(calls)

    async def result(owner, text, guard):
        batches.choose(config, binding, bid, "cancel")
        guard()
        pytest.fail("cancelled result reached sender")

    with pytest.raises(InvalidInput, match="not confirmed"):
        asyncio.run(outbound.drain(config, binding, normal, maximum=1, result_sender=result))
    assert calls == []
    assert rows(config, "wx_task_deliveries")[0]["state"] == "unknown"


def test_changed_pending_receipt_stops_instead_of_overwrite(config, binding, policy):
    bid, _ = task(config, binding, policy)
    delivery.prepare(config, binding, bid)
    with inbox.connection(config, binding) as db:
        db.execute("UPDATE wx_task_deliveries SET body='changed' WHERE batch_id=?", (bid,))
    adapter = configured(config, binding, [])

    async def forbidden(*args, **kwargs):
        pytest.fail("corrupt receipt must stop before fetch")

    asyncio.run(receiver.run(adapter, forbidden))
    assert adapter.errors[0][0] == "fiber_weixin_outbound_failed"


def test_incomplete_main_submission_skipped_until_recovered(config, binding, policy):
    with patch.object(ledger, "create_run", side_effect=RuntimeError("synthetic interruption")):
        with pytest.raises(RuntimeError):
            task(config, binding, policy, complete=False)
    assert queue.next_batch(config, binding) is None
    with inbox.connection(config, binding) as db:
        bid = db.execute("SELECT batch_id FROM wx_task_bindings").fetchone()[0]
    run_id = bindings.submit(config, binding, bid, policy)["run_id"]
    db = database.connect(config.state_root)
    ledger.transition(db, run_id, "failed")
    db.close()
    assert queue.next_batch(config, binding) == bid


def test_candidate_forwards_send_guard(config, binding, policy, monkeypatch):
    import ast
    import hashlib

    from test_weixin_outbound import Response, adapter_for

    from fiber_bundle import weixin_hermes

    source = b"""_LIVE_ADAPTERS = {}
class WeixinAdapter:
    async def connect(self):
        try:
            if not self._acquire_platform_lock('weixin-bot-token', self._token, 'Weixin bot token'):
                return False
        except Exception as exc:
            logger.debug("[%s] Token lock unavailable (non-fatal): %s", self.name, exc)
    async def disconnect(self):
        _LIVE_ADAPTERS.pop(self._token, None)
    async def _poll_loop(self):
        pass
"""
    monkeypatch.setattr(weixin_hermes, "SOURCE_SHA256", hashlib.sha256(source).hexdigest())
    tree = ast.parse(weixin_hermes.candidate(source))
    namespace = {"_headers": lambda *a: {}, "_base_info": lambda: {}}
    exec(compile(tree, "synthetic-candidate", "exec"), namespace)
    bid, _ = task(config, binding, policy)
    calls = []
    adapter = adapter_for(config, binding, Response(b'{"ret":0}'), calls)
    method = namespace["WeixinAdapter"]._fiber_send_result_once
    adapter._fiber_send_result_once = method.__get__(adapter)
    adapter._fiber_send_once = senders([])[0]
    adapter._running = False
    receiver.configure(adapter, config, binding, outbound_limit=1, result_receipts=True)
    adapter._running = True

    async def fetch(*args, **kwargs):
        adapter._running = False
        return poll(cursor="candidate-result")

    asyncio.run(receiver.run(adapter, fetch))
    assert len(calls) == 1 and not adapter.errors
    assert delivery.prepare(config, binding, bid)["state"] == "sent"


def test_excess_bindings_are_corruption_not_pending(config, binding, policy):
    bid, _ = task(config, binding, policy)
    with inbox.connection(config, binding) as db:
        db.execute(
            "INSERT INTO wx_task_bindings SELECT 'unexpected-run',account_id,batch_id,"
            "'unexpected-input',kind,policy_digest,plan_digest,request_json "
            "FROM wx_task_bindings WHERE batch_id=?",
            (bid,),
        )
    with pytest.raises(InvalidInput, match="scope changed"):
        queue.next_batch(config, binding)
