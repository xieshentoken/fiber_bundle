"""Batch result receipts through a synthetic one-shot transport and real isolated ledgers."""

import asyncio
import json
from dataclasses import replace

import pytest
from test_answer_tasks import invoke
from test_execution import policy as policy
from test_weixin_inbox import binding as binding
from test_weixin_inbox import rows
from test_weixin_outbound import Response, adapter_for
from test_weixin_task_bindings import setup
from test_weixin_task_dispatch import queued, two_articles
from test_weixin_task_inputs import CAPTURE, query_grant, saved

from fiber_bundle import auth, database
from fiber_bundle import execution_ledger as ledger
from fiber_bundle import weixin_batches as batches
from fiber_bundle import weixin_hermes_sender as transport
from fiber_bundle import weixin_inbox as inbox
from fiber_bundle import weixin_task_bindings as bindings
from fiber_bundle import weixin_task_delivery as delivery
from fiber_bundle.config import IntegrityError, InvalidInput


def completed(config, binding, policy):
    bid, run_id = queued(config, binding, policy)
    db = database.connect(config.state_root)
    ledger.start(config, db, run_id, policy)
    invoke(config, policy, run_id)
    ledger.transition(db, run_id, "succeeded")
    db.close()
    return bid, run_id


def sender(adapter):
    async def send(owner, text, guard):
        return await transport.send_once(
            adapter,
            owner,
            text,
            headers=lambda token, body: {},
            base_info=lambda: {},
            authorize=guard,
        )

    return send


def test_answer_citations_ack_and_restart_idempotency(config, binding, policy):
    bid, run_id = completed(config, binding, policy)
    prepared = delivery.prepare(config, binding, bid)
    assert prepared == delivery.prepare(config, binding, bid)
    assert "原文片段" in prepared["text"] and "合成正文" in prepared["text"]
    assert "会话资料" in prepared["text"] and "未判断资料时效" in prepared["text"]
    calls = []
    adapter = adapter_for(config, binding, Response(b'{"ret":0}'), calls)
    assert asyncio.run(delivery.deliver(config, binding, bid, sender(adapter)))["state"] == "sent"
    assert asyncio.run(delivery.deliver(config, binding, bid, sender(adapter)))["state"] == "sent"
    assert len(calls) == 1
    msg = json.loads(calls[0][1]["data"])["msg"]
    assert msg["to_user_id"] == binding.owner_id
    assert msg["item_list"][0]["text_item"]["text"] == prepared["text"]
    stored = rows(config, "wx_task_deliveries")[0]
    assert stored["state"] == "sent" and "private-route" not in json.dumps(stored)
    assert run_id in prepared["text"]


def test_all_tasks_required_and_failures_merged_without_error_text(config, binding, policy):
    bid, runs = two_articles(config, binding, policy)
    db = database.connect(config.state_root)
    ledger.transition(db, runs[0], "failed", "synthetic-secret-path")
    with pytest.raises(InvalidInput, match="not ready"):
        delivery.prepare(config, binding, bid)
    ledger.transition(db, runs[1], "failed", "synthetic-secret-token")
    db.close()
    prepared = delivery.prepare(config, binding, bid)
    assert prepared["text"].count("执行失败") == 2
    assert all(run in prepared["text"] for run in runs)
    assert "synthetic-secret" not in prepared["text"]


def test_missing_submission_and_missing_validated_answer_refused(config, binding, policy):
    bid = setup(config, binding)
    with pytest.raises(InvalidInput):
        delivery.prepare(config, binding, bid)
    run_id = bindings.submit(config, binding, bid, policy)["run_id"]
    db = database.connect(config.state_root)
    ledger.transition(db, run_id, "succeeded")
    db.close()
    with pytest.raises(InvalidInput, match="not ready"):
        delivery.prepare(config, binding, bid)


def test_article_success_does_not_claim_published(config, binding, policy):
    bid, runs = two_articles(config, binding, policy)
    db = database.connect(config.state_root)
    for run_id in runs:
        db.execute(
            "UPDATE article_tasks SET state='captured',image_count=0,markdown_bytes=1,"
            "manifest_sha256=? WHERE run_id=?",
            ("a" * 64, run_id),
        )
        ledger.transition(db, run_id, "succeeded")
    db.close()
    prepared = delivery.prepare(config, binding, bid)
    assert prepared["text"].count("文章提取完成") == 2
    assert "发布需另行核验" in prepared["text"] and "已发布" not in prepared["text"]


def test_partially_submitted_article_batch_not_ready(config, binding, policy):
    bid, handoff = saved(
        config, binding, "save_separately", ["https://x.com/a/status/1", "https://x.com/a/status/2"]
    )
    run_id = bindings.submit(
        config,
        binding,
        bid,
        policy,
        capture_policy=CAPTURE,
        parent_receipt_id=handoff["receipts"][0]["job_id"],
    )["run_id"]
    db = database.connect(config.state_root)
    ledger.transition(db, run_id, "failed")
    db.close()
    with pytest.raises(InvalidInput, match="submissions incomplete"):
        delivery.prepare(config, binding, bid)


@pytest.mark.parametrize("ack", [b'{"ret":-1}', b"{}", b'{"ret":false}'])
def test_unknown_ack_never_retried(config, binding, policy, ack):
    bid, _ = completed(config, binding, policy)
    calls = []
    adapter = adapter_for(config, binding, Response(ack), calls)
    for _ in range(2):
        assert (
            asyncio.run(delivery.deliver(config, binding, bid, sender(adapter)))["state"]
            == "unknown"
        )
    assert len(calls) == 1


def test_parallel_deliver_claims_once(config, binding, policy):
    bid, _ = completed(config, binding, policy)
    calls = []

    async def run():
        gate = asyncio.Event()

        async def send(owner, text, guard):
            guard()
            calls.append(text)
            await gate.wait()
            return True

        first = asyncio.create_task(delivery.deliver(config, binding, bid, send))
        await asyncio.sleep(0)
        second = await delivery.deliver(config, binding, bid, send)
        assert second["state"] == "sending"
        gate.set()
        assert (await first)["state"] == "sent"

    asyncio.run(run())
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["cancel", "query", "result"])
def test_send_gate_rechecks_before_any_post(config, binding, policy, change):
    bid, run_id = completed(config, binding, policy)
    calls = []

    async def run():
        adapter = adapter_for(config, binding, Response(b'{"ret":0}'), calls)
        await adapter._send_text_gate.acquire()
        task = asyncio.create_task(delivery.deliver(config, binding, bid, sender(adapter)))
        await asyncio.sleep(0)
        assert rows(config, "wx_task_deliveries")[0]["state"] == "sending"
        if change == "cancel":
            batches.choose(config, binding, bid, "cancel")
        else:
            db = database.connect(config.state_root)
            if change == "query":
                auth.set_grant(
                    db, binding.client_id, binding.vault_id, add=True, query=False, original=False
                )
            else:
                db.execute("UPDATE answer_tasks SET result_json='{}' WHERE run_id=?", (run_id,))
            db.close()
        adapter._send_text_gate.release()
        assert (await task)["state"] == "unknown"

    asyncio.run(run())
    assert not calls


def test_cancel_before_prepare_denies(config, binding, policy):
    bid, _ = completed(config, binding, policy)
    batches.choose(config, binding, bid, "cancel")
    with pytest.raises(InvalidInput):
        delivery.prepare(config, binding, bid)


def test_cancellation_in_flight_keeps_sending(config, binding, policy):
    bid, _ = completed(config, binding, policy)

    async def send(owner, text, guard):
        guard()
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(delivery.deliver(config, binding, bid, send))
    assert rows(config, "wx_task_deliveries")[0]["state"] == "sending"
    assert asyncio.run(delivery.deliver(config, binding, bid, send))["state"] == "sending"


def test_unchecked_ack_and_exception_are_unknown(config, binding, policy):
    bid, _ = completed(config, binding, policy)

    async def unchecked(owner, text, guard):
        return True

    assert asyncio.run(delivery.deliver(config, binding, bid, unchecked))["state"] == "unknown"
    assert "text" not in asyncio.run(delivery.deliver(config, binding, bid, unchecked))


def test_foreign_binding_and_tampered_receipt_denied(config, binding, policy):
    bid, _ = completed(config, binding, policy)
    other = replace(binding, account_id="other-account")
    inbox.initialize(config, other)
    with pytest.raises(InvalidInput):
        delivery.prepare(config, other, bid)
    delivery.prepare(config, binding, bid)
    with inbox.connection(config, binding) as db:
        db.execute("UPDATE wx_task_deliveries SET body='forged'")
    with pytest.raises(IntegrityError, match="changed"):
        delivery.prepare(config, binding, bid)


def test_over_limit_uses_complete_result_notice_not_partial_quotes(config, binding, policy):
    query_grant(config, binding)
    bid, _ = saved(
        config, binding, "consult", ["合成正文" + "原文材料" * 99 + str(i) for i in range(10)]
    )
    run_id = bindings.submit(config, binding, bid, policy)["run_id"]
    db = database.connect(config.state_root)
    ledger.start(config, db, run_id, policy)
    invoke(config, policy, run_id)
    ledger.transition(db, run_id, "succeeded")
    db.close()
    prepared = delivery.prepare(config, binding, bid)
    assert "本条未发送引文" in prepared["text"] and "原文片段" not in prepared["text"]
    assert len(prepared["text"].encode()) < 8192


def test_unknown_result_blocks_existing_outbound_queue(config, binding, policy):
    from fiber_bundle import weixin_outbound_dispatch as outbound

    bid, _ = completed(config, binding, policy)

    async def unknown(owner, text, guard):
        guard()
        return False

    assert asyncio.run(delivery.deliver(config, binding, bid, unknown))["state"] == "unknown"
    calls = []

    async def send(owner, text):
        calls.append(text)
        return True

    with pytest.raises(InvalidInput, match="unresolved"):
        asyncio.run(outbound.drain(config, binding, send, maximum=1))
    assert not calls


def test_existing_unknown_command_blocks_result_claim(config, binding, policy):
    bid, _ = completed(config, binding, policy)
    prepared = delivery.prepare(config, binding, bid)
    from test_weixin_confirmations import command

    from fiber_bundle import weixin_confirmations as confirmations
    from fiber_bundle import weixin_receipt_delivery as receipts

    job_id = command(config, binding, "批次 invalid 无效命令")
    confirmations.process(config, binding)
    receipts.prepare(config, binding, job_id)
    with inbox.connection(config, binding) as db:
        db.execute("UPDATE wx_receipt_deliveries SET state='unknown' WHERE job_id=?", (job_id,))
    calls = []

    async def send(owner, text, guard):
        calls.append(text)
        guard()
        return True

    with pytest.raises(InvalidInput, match="unresolved"):
        asyncio.run(delivery.deliver(config, binding, bid, send))
    assert not calls
    assert delivery.prepare(config, binding, bid) == prepared


def test_exception_retains_only_unknown_and_no_retry(config, binding, policy):
    bid, _ = completed(config, binding, policy)
    calls = []

    async def send(owner, text, guard):
        guard()
        calls.append(1)
        raise RuntimeError("synthetic-private-token")

    for _ in range(2):
        assert asyncio.run(delivery.deliver(config, binding, bid, send))["state"] == "unknown"
    assert calls == [1]
    assert "synthetic-private-token" not in json.dumps(rows(config, "wx_task_deliveries"))


def test_historical_note_has_title_and_digest_instead_of_empty_version(config, binding, policy):
    note = config.project_root / "test vault/temp_vault1/history.md"
    note.write_text("# 历史记录\n合成正文历史资料，没有日期。\n")
    bid, _ = completed(config, binding, policy)
    body = delivery.prepare(config, binding, bid)["text"]
    assert "历史记录" in body and "历史" in body
    assert "版本/指纹：None" not in body
    from hashlib import sha256

    assert sha256(note.read_bytes()).hexdigest() in body
