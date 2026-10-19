"""Conservative ingress routing with real journals/Store; synthetic owner messages only."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from test_execution import policy as policy
from test_weixin_batches import at, capture
from test_weixin_inbox import binding as binding
from test_weixin_inbox import message, poll, rows
from test_weixin_receiver import Adapter, revoke
from test_weixin_store import main_rows
from test_weixin_task_inputs import query_grant

from fiber_bundle import answer_tasks, auth, database, execution_ledger
from fiber_bundle import weixin_batches as batches
from fiber_bundle import weixin_confirmations as confirmations
from fiber_bundle import weixin_inbox as inbox
from fiber_bundle import weixin_intents as intents
from fiber_bundle import weixin_receiver as receiver
from fiber_bundle import weixin_store as handoff
from fiber_bundle import weixin_task_bindings as bindings
from fiber_bundle import weixin_task_inputs as task_inputs
from fiber_bundle import weixin_task_submission as submission
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.store import Store
from fiber_bundle.weixin_intent_text import classify


def event(text, **extra):
    return message(item_list=[{"type": 1, "text_item": {"text": text}}], **extra)


def initialize(config, binding):
    confirmations.initialize(config, binding)
    intents.initialize(config, binding)


def route(config, binding, texts, *, start=0):
    initialize(config, binding)
    for i, text in enumerate(texts):
        capture(
            config,
            binding,
            f"e-{start}-{i}",
            start + i,
            item_list=[{"type": 1, "text_item": {"text": text}}],
        )
    confirmations.process(config, binding, intent_routing=True)
    bids = batches.advance(config, binding, now=at(start + len(texts) + 3))
    # Freeze with the controlled clock, then run the normal restart-recoverable resolver.
    confirmations.tick(config, binding, intent_routing=True)
    return bids[0]["batch_id"]


def material_texts(config, receipt):
    db = database.connect(config.state_root)
    try:
        assets = db.execute(
            "SELECT a.id FROM version_assets v JOIN assets a ON a.id=v.asset_id "
            "WHERE v.version_id=? AND v.role='body' ORDER BY v.ordinal",
            (receipt["source_version_id"],),
        ).fetchall()
    finally:
        db.close()
    return [Store(config).read_asset(a[0], vault="personal").decode() for a in assets]


@pytest.mark.parametrize(
    "text",
    ["https://mp.weixin.qq.com/s/synthetic", "一份普通资料正文", "这里是第一段\n这里是第二段"],
)
def test_single_material_defaults_save_after_quiet_window(config, binding, text):
    initialize(config, binding)
    capture(config, binding, "material", 0, item_list=[{"type": 1, "text_item": {"text": text}}])
    confirmations.process(config, binding, intent_routing=True)
    bid = batches.advance(config, binding, now=at(2))[0]["batch_id"]
    assert batches.get(config, binding, bid)["state"] == "buffering"
    assert not main_rows(config, "receipts")
    batches.advance(config, binding, now=at(3))
    confirmations.tick(config, binding, intent_routing=True)
    assert batches.get(config, binding, bid)["action"] == "save_one"
    receipt = handoff.transfer(config, binding, bid)["receipts"][0]
    assert material_texts(config, receipt) == [text]
    assert main_rows(config, "source_versions")[0]["retention"] == "knowledge"
    assert not rows(config, "wx_confirmation_prompts")
    assert not main_rows(config, "model_calls")


@pytest.mark.parametrize("body", ["合成正文", "https://x.com/a/status/1"])
@pytest.mark.parametrize("question", ["请解释合成正文", "这份资料讲了什么？", "为什么有这个结论？"])
def test_material_plus_natural_question_session_only_and_question_not_evidence(
    config, binding, body, question, policy
):
    query_grant(config, binding)
    bid = route(config, binding, [body + "\n" + question])
    assert batches.get(config, binding, bid)["action"] == "consult"
    result = handoff.transfer(config, binding, bid)
    assert result["question"] == question and result["retention"] == "session"
    assert material_texts(config, result["receipts"][0]) == [body]
    run = submission.dispatch(config, binding, policy, maximum=1)["results"][0]
    assert run["kind"] == "answer" and run["state"] == "queued"
    assert {v["retention"] for v in main_rows(config, "source_versions")} == {"session"}
    assert not main_rows(config, "model_calls")


@pytest.mark.parametrize("suffix", ["，也保存原资料", "，同时保存", "。顺便保存原文。"])
def test_explicit_also_save_originals_and_consultation(config, binding, policy, suffix):
    query_grant(config, binding)
    question = "请解释合成正文" + suffix
    bid = route(config, binding, ["合成正文\n问题：" + question])
    assert batches.get(config, binding, bid)["action"] == "save_one"
    result = handoff.transfer(config, binding, bid)
    assert result["query_after_save"] and result["retention"] == "knowledge"
    assert material_texts(config, result["receipts"][0]) == ["合成正文\n"]
    run = submission.dispatch(config, binding, policy, maximum=1)["results"][0]
    assert run["kind"] == "answer"
    db = database.connect(config.state_root)
    try:
        answer = answer_tasks.get(db, run["run_id"])
        actual = answer_tasks.current(
            config, db, execution_ledger.get_run(db, run["run_id"]), answer
        )
        assert actual["sources"]
        assert all(question not in s["snippet"] for s in actual["sources"])
        assert actual["retrieval_query"] == "合成正文"
    finally:
        db.close()
    assert {v["retention"] for v in main_rows(config, "source_versions")} == {
        "session",
        "knowledge",
    }
    assert not main_rows(config, "model_calls")


def test_multi_material_then_question_requires_confirmation(config, binding, policy):
    query_grant(config, binding)
    bid = route(config, binding, ["合成正文第一份", "合成正文第二份", "请比较合成正文"])
    assert batches.get(config, binding, bid)["state"] == "frozen"
    assert len(rows(config, "wx_confirmation_prompts")) == 1
    assert "3 条消息，含 2 条原资料" in rows(config, "wx_confirmation_prompts")[0]["body"]
    assert not main_rows(config, "receipts")
    assert submission.dispatch(config, binding, policy, maximum=1)["processed"] == 0
    batches.choose(config, binding, bid, "consult")
    result = handoff.transfer(config, binding, bid)
    assert result["question"] == "请比较合成正文"
    assert material_texts(config, result["receipts"][0]) == ["合成正文第一份", "合成正文第二份"]
    assert result["retention"] == "session"


@pytest.mark.parametrize("action", ["save_one", "save_separately", "consult"])
def test_multisource_also_save_choice_and_one_full_scope_answer(config, binding, policy, action):
    query_grant(config, binding)
    bid = route(config, binding, ["合成正文一", "合成正文二", "请比较合成正文，也保存原资料"])
    assert batches.get(config, binding, bid)["state"] == "frozen"
    batches.choose(config, binding, bid, action)
    result = handoff.transfer(config, binding, bid)
    assert len(result["receipts"]) == (2 if action == "save_separately" else 1)
    assert result["retention"] == ("session" if action == "consult" else "knowledge")
    assert bool(result.get("query_after_save")) == (action != "consult")
    task = submission.dispatch(config, binding, policy, maximum=10)
    assert task["processed"] == 1 and task["results"][0]["kind"] == "answer"
    prepared = task_inputs.prepare_answer(config, binding, bid)
    parent = next(r for r in main_rows(config, "receipts") if r["job_id"] == prepared.receipt_id)
    assert material_texts(config, parent) == ["合成正文一", "合成正文二"]
    assert submission.dispatch(config, binding, policy, maximum=10)["processed"] == 0
    assert not main_rows(config, "model_calls")


def test_consultation_without_question_waits_then_accepts_bound_followup(config, binding):
    bid = route(config, binding, ["合成正文", "仅用于咨询"])
    selected = batches.choose(config, binding, bid, "consult")
    assert selected["needs_question"]
    with pytest.raises(InvalidInput):
        handoff.transfer(config, binding, bid)
    batches.choose(config, binding, bid, "consult", question="请解释合成正文")
    receipt = handoff.transfer(config, binding, bid)["receipts"][0]
    assert material_texts(config, receipt) == ["合成正文"]


@pytest.mark.parametrize("suffix", ["仅用于咨询", "不要保存", "只咨询"])
def test_same_message_consult_only_without_question_never_defaults_save(config, binding, suffix):
    bid = route(config, binding, ["合成正文\n" + suffix])
    assert batches.get(config, binding, bid)["state"] == "frozen"
    choice = batches.choose(config, binding, bid, "consult")
    assert choice["needs_question"]
    with pytest.raises(InvalidInput):
        handoff.transfer(config, binding, bid)
    batches.choose(config, binding, bid, "consult", question="请解释合成正文")
    result = handoff.transfer(config, binding, bid)
    assert result["retention"] == "session"
    assert material_texts(config, result["receipts"][0]) == ["合成正文"]


@pytest.mark.parametrize("marker", ["forwarded", "forward_info", "ref_msg"])
def test_forwarded_questions_and_save_instructions_remain_source_data(config, binding, marker):
    initialize(config, binding)
    body = "请解释合成正文，也保存原资料"
    capture(
        config,
        binding,
        "forward",
        0,
        **{marker: {"synthetic": True}},
        item_list=[{"type": 1, "text_item": {"text": body}}],
    )
    confirmations.process(config, binding, intent_routing=True)
    bid = batches.advance(config, binding, now=at(4))[0]["batch_id"]
    confirmations.tick(config, binding, intent_routing=True)
    result = handoff.transfer(config, binding, bid)
    assert result["question"] is None and not result.get("query_after_save")
    assert material_texts(config, result["receipts"][0]) == [body]


def test_referenced_question_is_material_and_outer_question_is_user_request(config, binding):
    initialize(config, binding)
    capture(
        config,
        binding,
        "reference",
        0,
        item_list=[
            {
                "type": 1,
                "text_item": {"text": "请解释合成正文"},
                "ref_msg": {
                    "message_item": {"type": 1, "text_item": {"text": "为什么原文这样问？"}}
                },
            }
        ],
    )
    confirmations.process(config, binding, intent_routing=True)
    bid = batches.advance(config, binding, now=at(4))[0]["batch_id"]
    confirmations.tick(config, binding, intent_routing=True)
    receipt = handoff.transfer(config, binding, bid)["receipts"][0]
    assert material_texts(config, receipt) == ["为什么原文这样问？"]


@pytest.mark.parametrize(
    "text",
    [
        "保存刚才那份",
        "这篇说什么？",
        "帮我看看",
        "清理会话",
        "不要保存这份资料",
        "请做一份PPT",
        "取消",
        "请归档刚才那份",
    ],
)
def test_missing_or_ambiguous_scope_stays_frozen_and_never_becomes_source(config, binding, text):
    bid = route(config, binding, [text])
    view = batches.get(config, binding, bid)
    assert view["state"] == "frozen" and not view["content_ready"]
    assert "请明确资料" in rows(config, "wx_confirmation_prompts")[0]["body"]
    for action in ("save_one", "save_separately", "consult"):
        with pytest.raises(InvalidInput):
            batches.choose(config, binding, bid, action)
    batches.choose(config, binding, bid, "cancel")
    assert not main_rows(config, "receipts")


def test_late_question_gets_candidates_without_expanding_old_frozen_scope(config, binding):
    old = route(config, binding, ["合成正文一", "合成正文二"])
    members = batches.get(config, binding, old)["members"]
    old_prompt = rows(config, "wx_confirmation_prompts")[0]["id"]
    new = route(config, binding, ["保存刚才那份"], start=100)
    assert new != old
    assert batches.get(config, binding, old)["members"] == members
    new_prompt = next(r for r in rows(config, "wx_confirmation_prompts") if r["batch_id"] == new)
    assert old_prompt in new_prompt["body"]
    assert batches.get(config, binding, old)["state"] == "frozen"
    assert not main_rows(config, "receipts")


@pytest.mark.parametrize(
    "text",
    ["正文\n请解释合成正文，也保存，但不要保存", "正文\n问题：", "正文\n问题：" + "问" * 4001],
)
def test_unclear_or_missing_question_does_not_auto_save(config, binding, text):
    bid = route(config, binding, [text])
    assert batches.get(config, binding, bid)["state"] == "frozen"
    assert not main_rows(config, "receipts")


def test_restart_replays_original_choice_without_duplicate_sources(config, binding):
    bid = route(config, binding, ["合成正文\n请解释合成正文"])
    first = handoff.transfer(config, binding, bid)
    confirmations.tick(config, binding, intent_routing=True)
    second = handoff.transfer(config, binding, bid)
    assert first == second and len(main_rows(config, "receipts")) == 1
    assert not main_rows(config, "model_calls")


def test_legacy_already_classified_sources_are_not_reinterpreted(config, binding):
    confirmations.initialize(config, binding)
    capture(
        config,
        binding,
        "old",
        0,
        item_list=[{"type": 1, "text_item": {"text": "请解释合成正文，也保存原资料"}}],
    )
    confirmations.process(config, binding)
    batches.advance(config, binding, now=at(4))
    confirmations.tick(config, binding, intent_routing=True)
    assert not rows(config, "wx_event_intents")
    assert batches.list_batches(config, binding)["batches"][0]["state"] == "frozen"


@pytest.mark.parametrize("table", ["wx_event_intents", "wx_batch_intents"])
def test_intent_tampering_stops_before_store(config, binding, table):
    bid = route(config, binding, ["合成正文"])
    with inbox.connection(config, binding) as db:
        column = "intent_json" if table == "wx_event_intents" else "plan_json"
        db.execute(f"UPDATE {table} SET {column}='{{}}'")
    with pytest.raises(IntegrityError):
        handoff.transfer(config, binding, bid)
    assert not main_rows(config, "receipts")


def test_cancel_and_query_revocation_block_saved_consultation(config, binding, policy):
    query_grant(config, binding)
    bid = route(config, binding, ["合成正文\n请解释合成正文，也保存"])
    handoff.transfer(config, binding, bid)
    run = bindings.submit(config, binding, bid, policy)["run_id"]
    batches.choose(config, binding, bid, "cancel")
    db = database.connect(config.state_root)
    try:
        with pytest.raises(auth.Denied):
            execution_ledger.authorize(config, db, execution_ledger.get_run(db, run))
    finally:
        db.close()
    assert not main_rows(config, "model_calls")


def test_question_about_saving_does_not_authorize_archival():
    classified = classify(event("https://x.com/a\n为什么要也保存？"))
    assert classified["question"] == "为什么要也保存？" and not classified["also_save"]


@pytest.mark.parametrize(
    "items", [[{"type": True}], [{"type": 1, "text_item": 9}], [{"type": 99}], []]
)
def test_unsupported_shapes_do_not_crash_classifier(items):
    intent = classify(message(item_list=items))
    assert not intent["source_keys"]


def test_receiver_default_off_then_controlled_routing_and_store(config, binding):
    initialize(config, binding)
    capture(
        config, binding, "material", 0, item_list=[{"type": 1, "text_item": {"text": "合成正文"}}]
    )
    adapter = Adapter()
    receiver.configure(adapter, config, binding, intent_routing=True, store_limit=1)
    adapter._running = True

    async def fetch(*args, **kwargs):
        adapter._running = False
        return poll(cursor="final")

    asyncio.run(receiver.run(adapter, fetch))
    assert not adapter.errors
    assert len(main_rows(config, "receipts")) == 1
    assert not main_rows(config, "execution_runs") and not main_rows(config, "model_calls")
    with pytest.raises(InvalidInput):
        receiver.configure(Adapter(), config, binding, intent_routing=1)


def test_revocation_during_routing_has_no_store_effect(config, binding):
    bid = route(config, binding, ["合成正文"])
    revoke(config, binding)
    with pytest.raises(auth.Denied):
        handoff.transfer(config, binding, bid)
    assert not main_rows(config, "receipts")


def test_multiple_paragraphs_then_question_and_separate_also_save_line(config, binding, policy):
    query_grant(config, binding)
    bid = route(config, binding, ["合成正文第一段\n合成正文第二段\n请解释合成正文\n也保存"])
    saved = handoff.transfer(config, binding, bid)
    assert saved["query_after_save"] and saved["question"] == "请解释合成正文"
    assert material_texts(config, saved["receipts"][0]) == ["合成正文第一段\n合成正文第二段"]
    assert submission.dispatch(config, binding, policy, maximum=1)["processed"] == 1
    assert not main_rows(config, "model_calls")


def test_media_with_owner_caption_question_keeps_only_original_media(config, binding):
    initialize(config, binding)
    received = inbox.receive(
        config,
        binding,
        "",
        poll(
            message(
                item_list=[
                    {"type": 1, "text_item": {"text": "这张图片有什么内容？"}},
                    {"type": 2, "image_item": {"synthetic": True}},
                ]
            )
        ),
        {"one": {"1": b"synthetic-media-bytes"}},
    )
    confirmations.tick(config, binding, intent_routing=True)
    bid = batches.list_batches(config, binding)["batches"][0]["batch_id"]
    assert batches.get(config, binding, bid)["state"] == "buffering"
    received_at = datetime.fromisoformat(rows(config, "events")[0]["received_at"])
    batches.advance(config, binding, now=received_at + timedelta(seconds=4))
    confirmations.tick(config, binding, intent_routing=True)
    assert batches.get(config, binding, bid)["action"] == "consult"
    receipt = handoff.transfer(config, binding, bid)["receipts"][0]
    assert not material_texts(config, receipt)
    db = database.connect(config.state_root)
    try:
        manifest = Store(config).verify_receipt_locked(db, receipt["job_id"])
    finally:
        db.close()
    assert len(manifest["assets"]) == 2  # sanitized event + one exact original attachment
    assert received["events"][0]["state"] == "pending"


def test_incomplete_media_cannot_auto_select_until_recovered(config, binding):
    initialize(config, binding)
    capture(config, binding, "incomplete", 0, item_list=[{"type": 2, "image_item": {}}])
    confirmations.process(config, binding, intent_routing=True)
    bid = batches.advance(config, binding, now=at(4))[0]["batch_id"]
    confirmations.tick(config, binding, intent_routing=True)
    assert batches.get(config, binding, bid)["state"] == "frozen"
    with pytest.raises(InvalidInput):
        handoff.transfer(config, binding, bid)
    assert not main_rows(config, "receipts")


def test_prompt_crash_recovers_same_frozen_plan(config, binding, monkeypatch):
    initialize(config, binding)
    capture(config, binding, "one", 0)
    capture(config, binding, "two", 1)
    confirmations.process(config, binding, intent_routing=True)
    bid = batches.advance(config, binding, now=at(4))[0]["batch_id"]
    original = confirmations.prepare

    def crash(*args, **kwargs):
        raise SystemExit("synthetic crash before prompt")

    monkeypatch.setattr(confirmations, "prepare", crash)
    with pytest.raises(SystemExit):
        confirmations.tick(config, binding, intent_routing=True)
    plan = rows(config, "wx_batch_intents")[0]
    monkeypatch.setattr(confirmations, "prepare", original)
    confirmations.tick(config, binding, intent_routing=True)
    assert rows(config, "wx_batch_intents")[0] == plan
    assert len(rows(config, "wx_confirmation_prompts")) == 1
    assert batches.get(config, binding, bid)["state"] == "frozen"


def test_question_only_candidate_list_is_account_scoped(config, binding):
    other = replace(binding, account_id="another-synthetic-account")
    inbox.initialize(config, other)
    confirmations.initialize(config, other)
    capture(config, other, "foreign-one", 0, to_user_id=other.account_id)
    capture(config, other, "foreign-two", 1, to_user_id=other.account_id)
    confirmations.process(config, other)
    foreign_bid = batches.advance(config, other, now=at(4))[0]["batch_id"]
    foreign = confirmations.prepare(config, other, foreign_bid)["prompt_id"]
    route(config, binding, ["这篇讲什么？"], start=100)
    own = next(
        p for p in rows(config, "wx_confirmation_prompts") if p["account_id"] == binding.account_id
    )
    assert foreign not in own["body"]


def test_query_after_save_result_scope_uses_one_batch_answer(config, binding, policy):
    from fiber_bundle import weixin_task_results as results

    query_grant(config, binding)
    bid = route(config, binding, ["合成正文一", "合成正文二", "请比较合成正文，也保存"])
    batches.choose(config, binding, bid, "save_separately")
    handoff.transfer(config, binding, bid)
    run = bindings.submit(config, binding, bid, policy)["run_id"]
    db = database.connect(config.state_root)
    try:
        execution_ledger.transition(db, run, "failed", "synthetic-failure")
        db.commit()
    finally:
        db.close()
    with inbox.connection(config, binding) as journal:
        text, fingerprint = results.snapshot_locked(config, journal, binding, bid)
    assert text.count("执行失败") == 1 and fingerprint
    assert not main_rows(config, "model_calls")


def test_mutated_saved_consultation_gate_denies_task(config, binding, policy):
    query_grant(config, binding)
    bid = route(config, binding, ["合成正文\n请解释合成正文，也保存"])
    handoff.transfer(config, binding, bid)
    run = bindings.submit(config, binding, bid, policy)["run_id"]
    with inbox.connection(config, binding) as journal:
        journal.execute("DELETE FROM wx_batch_intents")
    db = database.connect(config.state_root)
    try:
        with pytest.raises(auth.Denied):
            execution_ledger.authorize(config, db, execution_ledger.get_run(db, run))
    finally:
        db.close()
    assert not main_rows(config, "model_calls")


def test_direct_choice_before_resolver_preserves_routed_question_boundary(config, binding):
    initialize(config, binding)
    capture(
        config,
        binding,
        "source-question",
        0,
        item_list=[{"type": 1, "text_item": {"text": "合成正文\n请解释合成正文，也保存"}}],
    )
    confirmations.process(config, binding, intent_routing=True)
    bid = batches.advance(config, binding, now=at(4))[0]["batch_id"]
    assert not rows(config, "wx_batch_intents")
    batches.choose(config, binding, bid, "save_one")
    assert len(rows(config, "wx_batch_intents")) == 1
    result = handoff.transfer(config, binding, bid)
    assert result["query_after_save"]
    assert material_texts(config, result["receipts"][0]) == ["合成正文"]


@pytest.mark.parametrize("table", ["wx_event_intents", "wx_batch_intents"])
def test_deleted_intent_table_is_not_legacy_fallback(config, binding, table):
    bid = route(config, binding, ["合成正文\n请解释合成正文"])
    with inbox.connection(config, binding) as journal:
        journal.execute(f"DROP TABLE {table}")
    with pytest.raises(IntegrityError):
        handoff.transfer(config, binding, bid)
    assert not main_rows(config, "receipts")


def test_missing_declared_event_intent_never_restores_question_as_evidence(config, binding):
    bid = route(config, binding, ["合成正文", "请解释合成正文"])
    with inbox.connection(config, binding) as journal:
        journal.execute("DELETE FROM wx_event_intents")
    with pytest.raises(IntegrityError):
        batches.choose(config, binding, bid, "consult")
    assert not main_rows(config, "receipts")


def test_explicit_source_label_keeps_quoted_instructions_as_material(config, binding):
    body = "原文：请解释什么是知识，也保存原资料"
    bid = route(config, binding, [body])
    result = handoff.transfer(config, binding, bid)
    assert result["question"] is None and not result.get("query_after_save")
    assert material_texts(config, result["receipts"][0]) == [body]


def test_two_questions_require_one_explicit_question(config, binding):
    bid = route(config, binding, ["合成正文", "请解释合成正文", "这份资料有什么意义？"])
    assert batches.get(config, binding, bid)["state"] == "frozen"
    batches.choose(config, binding, bid, "consult")
    assert batches.get(config, binding, bid)["needs_question"]
    batches.choose(config, binding, bid, "consult", question="请解释合成正文")
    result = handoff.transfer(config, binding, bid)
    assert material_texts(config, result["receipts"][0]) == ["合成正文"]
