"""Isolated receiver-to-Store-to-local-executor-to-publication/query acceptance.

No gateway, real provider, Docker, CDN or phone: transports and article worker use fixtures.
The journals, Store, protocol subprocess, budget ledger, publication and send guards are real.
"""

import asyncio
import json
import sys
from dataclasses import replace
from unittest.mock import patch

import pytest
from test_answer_tasks import response
from test_article_tasks import fixture_worker
from test_articles import X_URL
from test_execution import policy as policy
from test_weixin_inbox import binding as binding
from test_weixin_inbox import message, poll, rows
from test_weixin_media_dispatch import good, media
from test_weixin_receiver import Adapter
from test_weixin_store import main_rows
from test_weixin_task_inputs import CAPTURE, query_grant
from test_weixin_worker import PROTOCOL

from fiber_bundle import article_worker, auth, database, execution, model_gateway
from fiber_bundle import execution_ledger as ledger
from fiber_bundle import weixin_batches as batches
from fiber_bundle import weixin_diagnostics as diagnostics
from fiber_bundle import weixin_media_dispatch as media_dispatch
from fiber_bundle import weixin_outbound_dispatch as outbound
from fiber_bundle import weixin_publication as publication
from fiber_bundle import weixin_publication_dispatch as publishing
from fiber_bundle import weixin_receiver as receiver
from fiber_bundle import weixin_task_delivery as delivery
from fiber_bundle import weixin_task_dispatch as dispatch
from fiber_bundle import weixin_worker as worker
from fiber_bundle.config import InvalidInput
from fiber_bundle.store import Store
from fiber_bundle.weixin_worker_config import WorkerConfig

ARTICLE_PROTOCOL = """
import json,sys
json.loads(sys.stdin.readline())
for kind in ['article_capture','model']:
    print(json.dumps({'type':kind,'id':kind,'body':{}}),flush=True)
    reply=json.loads(sys.stdin.readline())
    if not reply['ok']: sys.exit(2)
print(json.dumps({'type':'event','event':{'type':'agent_end','messages':[{'role':'assistant','stopReason':'stop'}]}}),flush=True)
sys.stdin.read()
"""


def text(event_id, body):
    return message(event_id, item_list=[{"type": 1, "text_item": {"text": body}}])


def receive(config, binding, steps=(), *, sends=None, **kwargs):
    """Finite real receiver, with a quiet window while the fake poll is outstanding."""
    adapter = Adapter()
    sent = [] if sends is None else sends

    async def normal(owner, body):
        assert owner == binding.owner_id
        sent.append(body)
        return True

    async def guarded(owner, body, guard):
        guard()
        return await normal(owner, body)

    adapter._fiber_send_once = normal
    adapter._fiber_send_result_once = guarded
    receiver.configure(adapter, config, binding, quiet_seconds=0.005, **kwargs)
    adapter._running = True
    pending = list(steps)
    empty = 0

    async def fetch(*args, **kw):
        nonlocal empty
        await asyncio.sleep(0.04)
        if pending:
            entry = pending.pop(0)
            msgs = entry() if callable(entry) else entry
        else:
            msgs = []
            empty += 1
            if empty == 2:
                adapter._running = False
        return poll(*msgs, cursor=kw["sync_buf"] + "x")

    asyncio.run(receiver.run(adapter, fetch))
    assert adapter.disconnected == 1 and adapter._poll_task is None
    return adapter, sent


def select(config, action="分别保存", question=None):
    prompt = rows(config, "wx_confirmation_prompts")[-1]
    body = f"批次 {prompt['id']} {action}"
    if question:
        body += "\n问题：" + question
    return [text("choice-" + prompt["id"], body)]


def execute(config, binding, policy, *, article=False, provider=None, maximum=1):
    settings = WorkerConfig(
        binding, enabled=True, maximum=maximum, poll_limit=maximum + 1, poll_seconds=0.01
    )
    with (
        patch.object(
            execution,
            "command",
            return_value=[sys.executable, "-u", "-c", ARTICLE_PROTOCOL if article else PROTOCOL],
        ),
        patch.object(execution, "stop"),  # Native protocol process cleanup is real; Docker absent.
        patch.object(article_worker, "execute_capture", side_effect=fixture_worker),
        patch.object(
            model_gateway,
            "complete",
            side_effect=provider or (lambda p, r, payload: response(payload)),
        ) as remote,
    ):
        result = worker.run(config, settings, policy)
    return result, remote.call_count


def article_response(p, r, payload):
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "synthetic capture complete"},
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 30},
    }


def consult(config, binding, policy, body="合成正文", *, attachment=False):
    query_grant(config, binding)
    msg = text("consult-source", body)
    if attachment:
        msg["item_list"].append(media())
    original_drain = media_dispatch.drain

    async def local_drain(*args, **kwargs):
        return await original_drain(*args, fetcher=good, **kwargs)

    with patch.object(media_dispatch, "drain", side_effect=local_drain):
        adapter, _ = receive(
            config,
            binding,
            [[msg], lambda: select(config, "仅用于咨询", "合成")],
            store_limit=1,
            submission_limit=1,
            execution_policy=policy,
            media_limit=1 if attachment else 0,
        )
    assert not adapter.errors
    bid = rows(config, "wx_batches")[0]["id"]
    return bid, rows(config, "wx_task_bindings")[0]["run_id"]


def test_full_article_publication_fixed_query_and_single_merged_receipt(config, binding, policy):
    original = [text("article-one", X_URL), text("article-two", X_URL)]
    adapter, sent = receive(
        config,
        binding,
        [original, original, lambda: select(config)],
        store_limit=1,
        submission_limit=2,
        execution_policy=policy,
        capture_policy=CAPTURE,
        outbound_limit=10,
    )
    assert not adapter.errors
    assert len(rows(config, "events")) == 3  # Two originals plus authenticated choice, no replay.
    bid = rows(config, "wx_batches")[0]["id"]
    runs = [r["run_id"] for r in rows(config, "wx_task_bindings")]
    assert len(runs) == 2 and not main_rows(config, "model_calls")
    publication.plan(config, binding, bid)
    result, calls = execute(
        config, binding, policy, article=True, maximum=2, provider=article_response
    )
    assert result["state"] == "limit_reached" and calls == 2
    assert all(r["state"] == "succeeded" for r in main_rows(config, "execution_runs"))
    one = publishing.dispatch(config, binding, maximum=1)
    with pytest.raises(InvalidInput, match="publication not ready"):
        delivery.prepare(config, binding, bid)
    two = publishing.dispatch(config, binding, maximum=1)
    published = one["results"][0]
    assert published["publication_id"] == two["results"][0]["publication_id"]
    adapter, sent = receive(config, binding, sends=sent, outbound_limit=10, result_receipts=True)
    assert not adapter.errors
    receipts = [t for t in sent if "任务回执" in t]
    assert len(receipts) == 1 and receipts[0].count("文章已发布") == 2
    assert rows(config, "wx_task_deliveries")[0]["state"] == "sent"
    receive(config, binding, sends=sent, outbound_limit=10, result_receipts=True)
    assert len([t for t in sent if "任务回执" in t]) == 1
    query_grant(config, binding)
    before = len(main_rows(config, "receipts"))
    adapter, _ = receive(
        config,
        binding,
        [[text("query", "查询本库：合成")]],
        intent_routing=True,
        store_limit=1,
        submission_limit=1,
        execution_policy=policy,
    )
    assert not adapter.errors and len(main_rows(config, "receipts")) == before
    result, calls = execute(config, binding, policy)
    assert result["state"] == "limit_reached" and calls == 1
    adapter, sent = receive(config, binding, sends=sent, outbound_limit=10, result_receipts=True)
    assert not adapter.errors
    reply = [t for t in sent if "问答完成" in t][0]
    assert "原文片段" in reply and "行：" in reply
    assert published["source_version_id"] in reply
    assert len(main_rows(config, "model_calls")) == 3
    assert all(r["state"] == "settled" for r in main_rows(config, "model_calls"))
    assert sum(r["charged_nusd"] for r in main_rows(config, "model_calls")) == 54000
    assert len(main_rows(config, "article_publications")) == 1
    assert len(main_rows(config, "article_publication_runs")) == 2


def test_attachment_consultation_original_bytes_no_knowledge_publication(config, binding, policy):
    bid, rid = consult(config, binding, policy, attachment=True)
    assert rows(config, "wx_download_attempts")[0]["state"] == "succeeded"
    recovered = rows(config, "wx_recovered_media")[0]
    assert recovered["data"] == b"synthetic original bytes"
    original = rows(config, "events")[0]
    assert original["state"] == "incomplete"  # Original event never rewritten.
    result, calls = execute(config, binding, policy)
    assert result["state"] == "limit_reached" and calls == 1
    adapter, sent = receive(config, binding, outbound_limit=10, result_receipts=True)
    assert not adapter.errors and any("问答完成" in body for body in sent)
    assert rows(config, "wx_task_deliveries")[0]["batch_id"] == bid
    assert not main_rows(config, "article_publications")
    assert not list((config.project_root / "test vault").rglob("*.md"))
    assert (
        diagnostics.inspect(config, binding, batch_id=bid)["batches"][0]["tasks"]["items"][0][
            "run_id"
        ]
        == rid
    )


@pytest.mark.parametrize("mode", ["budget", "cancel", "revoke", "provider_unknown"])
def test_full_consult_failure_keeps_effects_and_never_replays(config, binding, policy, mode):
    if mode == "budget":
        policy = replace(policy, task_limit_nusd=1)
    bid, rid = consult(config, binding, policy)

    def remote(p, r, payload):
        if mode == "cancel":
            batches.choose(config, binding, bid, "cancel")
        elif mode == "revoke":
            db = database.connect(config.state_root)
            auth.set_grant(
                db, binding.client_id, binding.vault_id, add=True, query=False, original=False
            )
            db.close()
        elif mode == "provider_unknown":
            raise OSError("synthetic private provider token")
        return response(payload)

    result, calls = execute(config, binding, policy, provider=remote)
    expected = {
        "budget": "waiting_budget",
        "cancel": "failed",  # The task guard rejects the cancelled batch before accepting output.
        "revoke": "failed",
        "provider_unknown": "failed",
    }[mode]
    assert result["results"][0]["run_state"] == expected
    if mode == "cancel":
        assert batches.get(config, binding, bid)["state"] == "cancelled"
        assert main_rows(config, "execution_runs")[0]["error_code"] == "execution_failed"
    assert calls == (0 if mode == "budget" else 1)
    fees = main_rows(config, "model_calls")
    if fees:
        assert fees[0]["state"] == ("uncertain" if mode == "provider_unknown" else "settled")
    with patch.object(execution, "execute") as executor:
        dispatch.dispatch(config, binding, policy, maximum=1)
        executor.assert_not_called()
    assert fees == main_rows(config, "model_calls")
    assert len(rows(config, "wx_task_bindings")) == 1
    view = diagnostics.inspect(config, binding, batch_id=bid)
    assert "synthetic private provider token" not in json.dumps(view)
    if mode == "budget":
        assert (
            "review_budget_keep_existing_run_and_fees"
            in view["batches"][0]["tasks"]["items"][0]["actions"]
        )
    if mode == "revoke":
        assert (
            "review_current_query_permission_do_not_execute_or_deliver"
            in view["batches"][0]["tasks"]["items"][0]["actions"]
        )
    if mode in {"cancel", "revoke", "budget"}:
        with pytest.raises((InvalidInput, auth.Denied)):
            delivery.prepare(config, binding, bid)
    else:
        assert "执行失败" in delivery.prepare(config, binding, bid)["text"]


def test_send_effect_then_unknown_blocks_restart_but_capture_continues(config, binding, policy):
    bid, _ = consult(config, binding, policy)
    execute(config, binding, policy)
    effects = []

    async def unknown(owner, body, guard):
        guard()
        effects.append(body)  # Transport side effect can precede the missing acknowledgement.
        raise OSError("synthetic private send failure")

    assert asyncio.run(delivery.deliver(config, binding, bid, unknown))["state"] == "unknown"
    assert len(effects) == 1

    async def normal(*args):
        pytest.fail("unknown sends must never be retried")

    with pytest.raises(InvalidInput, match="unresolved"):
        asyncio.run(outbound.drain(config, binding, normal, maximum=10, result_sender=unknown))
    adapter, _ = receive(config, binding, [[text("later", "另一份资料")]], intent_routing=True)
    assert not adapter.errors and len(rows(config, "events")) == 3
    assert diagnostics.inspect(config, binding)["outbound_blockers"][0]["items"][0]["id"] == bid
    assert len(effects) == 1 and len(main_rows(config, "model_calls")) == 1


def test_capture_and_reserved_fee_survive_executor_crash_before_settlement(config, binding, policy):
    bid, rid = consult(config, binding, policy)

    def interrupted(config, policy, run_id, prompt):
        db = database.connect(config.state_root)
        ledger.start(config, db, run_id, policy)
        ledger.reserve(config, db, run_id, policy)
        db.close()
        raise KeyboardInterrupt

    with (
        patch.object(execution, "execute", side_effect=interrupted),
        pytest.raises(KeyboardInterrupt),
    ):
        dispatch.dispatch(config, binding, policy, maximum=1)
    before = main_rows(config, "model_calls")
    assert before[0]["state"] == "reserved"
    view = diagnostics.inspect(config, binding, batch_id=bid)["batches"][0]["tasks"]["items"][0]
    assert view["dispatch_phase"] == "claimed" and view["state"] == "running"
    assert view["fees"]["accounted_nusd"] == policy.reserve_nusd
    with patch.object(execution, "execute") as executor:
        assert dispatch.dispatch(config, binding, policy, maximum=1)["state"] == "blocked"
        assert dispatch.reconcile(config, binding, rid)["dispatch_phase"] == "unknown"
        executor.assert_not_called()
    assert before == main_rows(config, "model_calls")
    assert len(main_rows(config, "execution_runs")) == 1


def test_long_message_exact_store_and_unknown_card_quarantine(config, binding):
    body = "长正文资料\n" * 2000
    adapter, _ = receive(
        config, binding, [[text("long", body)]], intent_routing=True, store_limit=1
    )
    assert not adapter.errors
    receipt = main_rows(config, "receipts")[0]
    db = database.connect(config.state_root)
    asset = db.execute(
        "SELECT asset_id FROM version_assets WHERE version_id=?", (receipt["source_version_id"],)
    ).fetchone()[0]
    db.close()
    assert Store(config).read_asset(asset, vault="personal").decode() == body
    card = message(
        "card", item_list=[{"type": 99, "card_item": {"title": "保存为一份", "url": X_URL}}]
    )
    adapter, _ = receive(config, binding, [[card]], intent_routing=True, store_limit=1)
    assert not adapter.errors
    assert rows(config, "events")[-1]["state"] == "unsupported"
    assert len(main_rows(config, "receipts")) == 1
    assert not main_rows(config, "model_calls")
    assert any(b["unsupported_count"] == 1 for b in diagnostics.inspect(config, binding)["batches"])


def test_long_merged_answer_sends_notice_without_partial_quotes(config, binding, policy):
    query_grant(config, binding)
    sources = [text(f"long-{i}", "合成正文" + "原文材料" * 99 + str(i)) for i in range(10)]
    adapter, _ = receive(
        config,
        binding,
        [sources, lambda: select(config, "仅用于咨询", "合成")],
        store_limit=1,
        submission_limit=1,
        execution_policy=policy,
    )
    assert not adapter.errors
    result, calls = execute(config, binding, policy)
    assert result["state"] == "limit_reached" and calls == 1
    adapter, sent = receive(config, binding, outbound_limit=10, result_receipts=True)
    assert not adapter.errors
    body = next(t for t in sent if "任务回执" in t)
    assert "本条未发送引文" in body and "原文片段" not in body
    assert len(body.encode()) < 8192


def test_partial_article_failure_merges_actual_states_keeps_both_fees(config, binding, policy):
    adapter, _ = receive(
        config,
        binding,
        [[text("first", X_URL), text("second", X_URL)], lambda: select(config)],
        store_limit=1,
        submission_limit=2,
        execution_policy=policy,
        capture_policy=CAPTURE,
    )
    assert not adapter.errors
    count = 0

    def remote(p, r, payload):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("synthetic private second provider failure")
        return article_response(p, r, payload)

    result, calls = execute(config, binding, policy, article=True, maximum=2, provider=remote)
    assert result["state"] == "stopped" and calls == 2
    assert [r["state"] for r in main_rows(config, "execution_runs")] == ["succeeded", "failed"]
    assert [r["state"] for r in main_rows(config, "model_calls")] == ["settled", "uncertain"]
    adapter, sent = receive(config, binding, outbound_limit=10, result_receipts=True)
    assert not adapter.errors
    body = next(t for t in sent if "任务回执" in t)
    assert "提取完成" in body and "执行失败" in body and "文章已发布" not in body
    assert "synthetic private" not in body
    assert len(rows(config, "wx_task_deliveries")) == 1
