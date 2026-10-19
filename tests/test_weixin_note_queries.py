"""Explicit existing-note queries from a fixed Weixin binding, using M5 evidence contracts."""

import asyncio
import json
from unittest.mock import patch

import pytest
from test_answer_tasks import invoke
from test_execution import policy as policy
from test_weixin_inbox import binding as binding
from test_weixin_intents import route
from test_weixin_publication import articles, complete, note
from test_weixin_store import main_rows
from test_weixin_task_inputs import query_grant

from fiber_bundle import answer_tasks, auth, database, execution
from fiber_bundle import execution_ledger as ledger
from fiber_bundle import weixin_batches as batches
from fiber_bundle import weixin_inbox as inbox
from fiber_bundle import weixin_publication as publication
from fiber_bundle import weixin_publication_dispatch as publishing
from fiber_bundle import weixin_result_queue as result_queue
from fiber_bundle import weixin_store as handoff
from fiber_bundle import weixin_task_bindings as bindings
from fiber_bundle import weixin_task_delivery as delivery
from fiber_bundle import weixin_task_dispatch as dispatch
from fiber_bundle import weixin_task_status as status
from fiber_bundle import weixin_task_submission as submission
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.operations import original


def published(config, binding, policy):
    bid, runs = articles(config, binding, policy)
    publication.plan(config, binding, bid)
    complete(config, policy, runs[0])
    return publishing.dispatch(config, binding, maximum=1)["results"][0]


def query(config, binding, policy, *, text="查询本库：合成", start=10):
    query_grant(config, binding)
    bid = route(config, binding, [text], start=start)
    transferred = handoff.transfer(config, binding, bid)
    assert transferred["query_existing"] and transferred["receipts"] == []
    result = submission.dispatch(config, binding, policy, maximum=1)
    return bid, result["results"][0]["run_id"]


def finish(config, binding, policy, bid, rid):
    db = database.connect(config.state_root)
    ledger.start(config, db, rid, policy)
    db.close()
    invoke(config, policy, rid)
    db = database.connect(config.state_root)
    ledger.transition(db, rid, "succeeded")
    db.close()
    return delivery.prepare(config, binding, bid)


def test_published_article_to_explicit_weixin_query_quotes_and_original_permission(
    config, binding, policy
):
    result = published(config, binding, policy)
    before = {
        p: (p.read_bytes(), p.stat().st_ino)
        for p in note(config, result).parent.rglob("*")
        if p.is_file()
    }
    previous_receipts = len(main_rows(config, "receipts"))
    bid, rid = query(config, binding, policy)
    assert len(main_rows(config, "receipts")) == previous_receipts
    assert bindings.submit(config, binding, bid, policy)["run_id"] == rid
    assert submission.dispatch(config, binding, policy, maximum=3)["processed"] == 0
    task = main_rows(config, "answer_tasks")[0]
    assert task["receipt_id"] is None and task["input_kind"] == "notes"
    assert json.loads(task["request_json"])["session_id"] is None
    prepared = finish(config, binding, policy, bid, rid)
    assert "原文片段" in prepared["text"] and "合成" in prepared["text"]
    assert result["source_version_id"] in prepared["text"]
    assert "查询本库" not in prepared["text"] and "行：" in prepared["text"]
    assert status.synchronize(config, binding, rid)["run_state"] == "succeeded"
    db = database.connect(config.state_root)
    answer = answer_tasks.deliver(config, db, binding.client_id, rid)
    assert answer["searched_vault_ids"] == [binding.vault_id]
    assert answer["sources"][0]["source_kind"] == "published_article"
    assert answer["sources"][0]["originals"] == []
    asset = db.execute(
        "SELECT asset_id FROM version_assets WHERE version_id=? AND original_name='source.html'",
        (result["archive_version_id"],),
    ).fetchone()[0]
    with pytest.raises(auth.Denied):
        original(
            config,
            db,
            binding.client_id,
            binding.vault_id,
            result["archive_version_id"],
            asset,
            1048576,
        )
    auth.set_grant(db, binding.client_id, binding.vault_id, add=True, query=True, original=True)
    assert b"Synthetic" in original(
        config,
        db,
        binding.client_id,
        binding.vault_id,
        result["archive_version_id"],
        asset,
        1048576,
    )
    db.close()
    assert before == {p: (p.read_bytes(), p.stat().st_ino) for p in before}


def test_native_dispatch_uses_fixed_note_evidence_and_charges_once(config, binding, policy):
    result = published(config, binding, policy)
    bid, rid = query(config, binding, policy)

    def executor(config, policy, rid, prompt):
        db = database.connect(config.state_root)
        ledger.start(config, db, rid, policy)
        db.close()
        invoke(config, policy, rid)
        db = database.connect(config.state_root)
        ledger.transition(db, rid, "succeeded")
        db.close()
        return {"state": "succeeded"}

    with patch.object(execution, "execute", side_effect=executor) as execute:
        assert dispatch.dispatch(config, binding, policy, maximum=1)["processed"] == 1
        assert dispatch.dispatch(config, binding, policy, maximum=1)["processed"] == 0
        assert execute.call_count == 1
    assert len(main_rows(config, "model_calls")) == 1
    assert result["source_version_id"] in delivery.prepare(config, binding, bid)["text"]
    assert result_queue.next_batch(config, binding) is not None


@pytest.mark.parametrize("stage", ["before_execution", "before_delivery", "send_gate"])
@pytest.mark.parametrize("change", ["cancel", "query", "client"])
def test_query_cancellation_revocation_and_fees_preserved(config, binding, policy, stage, change):
    published(config, binding, policy)
    bid, rid = query(config, binding, policy)
    if stage != "before_execution":
        finish(config, binding, policy, bid, rid)
    old_fees = main_rows(config, "model_calls")

    def revoke():
        if change == "cancel":
            batches.choose(config, binding, bid, "cancel")
        else:
            db = database.connect(config.state_root)
            if change == "query":
                auth.set_grant(
                    db, binding.client_id, binding.vault_id, add=True, query=False, original=False
                )
            else:
                auth.revoke_client(db, binding.client_id)
            db.close()

    if stage == "send_gate":
        effects = []

        async def send(owner, text, guard):
            revoke()
            guard()
            effects.append(text)
            return True

        # Revoked add/client may also deny the final state update; sending remains unresolved.
        try:
            result = asyncio.run(delivery.deliver(config, binding, bid, send))
            assert result["state"] == "unknown"
        except auth.Denied:
            pass
        assert not effects
    else:
        revoke()
        with pytest.raises((auth.Denied, InvalidInput)):
            if stage == "before_execution":
                db = database.connect(config.state_root)
                try:
                    ledger.start(config, db, rid, policy)
                finally:
                    db.close()
            else:
                delivery.prepare(config, binding, bid)
    assert main_rows(config, "model_calls") == old_fees


@pytest.mark.parametrize("change", ["edit", "delete", "new_match"])
def test_note_scope_change_blocks_old_snapshot_and_preserves_human_edits(
    config, binding, policy, change
):
    result = published(config, binding, policy)
    bid, rid = query(config, binding, policy)
    finish(config, binding, policy, bid, rid)
    fees = main_rows(config, "model_calls")
    path = note(config, result)
    if change == "delete":
        path.unlink()
    elif change == "edit":
        path.write_text("# 人工修订\n合成人工数据 90 psi\n")
    else:
        (path.parent.parent / "extra.md").write_text("# 合成\n合成第二份资料\n")
    with pytest.raises(IntegrityError, match="evidence changed"):
        delivery.prepare(config, binding, bid)
    assert path.read_text().startswith("# 人工修订") if change == "edit" else True
    assert main_rows(config, "model_calls") == fees
    assert fees[0]["state"] == "settled" and fees[0]["charged_nusd"] > 0


def test_explicit_query_has_no_evidence_for_question_and_no_other_vault_expansion(
    config, binding, policy
):
    query_grant(config, binding)
    db = database.connect(config.state_root)
    other = db.execute("SELECT id FROM vaults WHERE name='supplier'").fetchone()[0]
    auth.set_grant(db, binding.client_id, other, add=True, query=True, original=True)
    db.close()
    (config.project_root / "test vault/temp_vault2/other.md").write_text("# 合成\n合成外库资料\n")
    bid, rid = query(config, binding, policy)
    prepared = finish(config, binding, policy, bid, rid)
    assert "原文片段" not in prepared["text"] and "外库资料" not in prepared["text"]
    assert not main_rows(config, "receipts")
    snapshot = json.loads(main_rows(config, "answer_tasks")[0]["snapshot_json"])
    assert snapshot["schema"] == "weixin-note-evidence/v1" and snapshot["evidence"] == []


def test_unscoped_and_forwarded_questions_keep_original_routing(config, binding):
    bid = route(config, binding, ["合成资料讲了什么？"], start=10)
    assert batches.get(config, binding, bid)["state"] == "frozen"
    with pytest.raises(InvalidInput):
        handoff.transfer(config, binding, bid)
    from fiber_bundle.weixin_intent_text import classify

    value = classify(
        {"forwarded": True, "item_list": [{"type": 1, "text_item": {"text": "查询本库：合成"}}]}
    )
    assert value["question"] is None and value["source_keys"]


def test_note_query_plan_digest_and_binding_tamper_refused(config, binding, policy):
    bid, rid = query(config, binding, policy)
    with inbox.connection(config, binding) as db:
        db.execute("UPDATE wx_task_bindings SET input_receipt_id='changed' WHERE run_id=?", (rid,))
    db = database.connect(config.state_root)
    try:
        with pytest.raises(auth.Denied):
            ledger.authorize(config, db, ledger.get_run(db, rid))
    finally:
        db.close()


def test_new_query_labels_human_revision_without_changing_note(config, binding, policy):
    result = published(config, binding, policy)
    path = note(config, result)
    path.write_text("# 人工修订\n合成实验压力 90 psi，需核对。\n")
    before = path.read_bytes()
    bid, rid = query(config, binding, policy)
    text = finish(config, binding, policy, bid, rid)["text"]
    assert "用户修订" in text and "90 psi" in text
    assert result["source_version_id"] in text
    assert path.read_bytes() == before


@pytest.mark.parametrize("change", ["binding", "handoff", "intent"])
def test_note_query_missing_link_is_denied_even_after_submission(config, binding, policy, change):
    bid, rid = query(config, binding, policy)
    with inbox.connection(config, binding) as db:
        if change == "binding":
            db.execute("DELETE FROM wx_task_bindings WHERE run_id=?", (rid,))
        elif change == "handoff":
            db.execute("DELETE FROM wx_store_handoffs WHERE batch_id=?", (bid,))
        else:
            db.execute("DELETE FROM wx_batch_intents WHERE batch_id=?", (bid,))
    db = database.connect(config.state_root)
    try:
        with pytest.raises(auth.Denied):
            ledger.authorize(config, db, ledger.get_run(db, rid))
    finally:
        db.close()


@pytest.mark.parametrize("mode", ["success", "budget", "cancel"])
def test_native_note_worker_protocol_uses_host_evidence(config, binding, policy, mode):
    import sys
    from dataclasses import replace

    from test_answer_tasks import response
    from test_weixin_worker import PROTOCOL

    from fiber_bundle import model_gateway
    from fiber_bundle import weixin_worker as worker
    from fiber_bundle.weixin_worker_config import WorkerConfig

    result = published(config, binding, policy)
    if mode == "budget":
        policy = replace(policy, task_limit_nusd=1)
    bid, rid = query(config, binding, policy)

    def provider(p, r, payload):
        assert "UNTRUSTED PI CONTEXT" not in json.dumps(payload)
        if mode == "cancel":
            batches.choose(config, binding, bid, "cancel")
            status.synchronize(config, binding, rid)
        return response(payload)

    with (
        patch.object(execution, "command", return_value=[sys.executable, "-u", "-c", PROTOCOL]),
        patch.object(execution, "stop"),
        patch.object(model_gateway, "complete", side_effect=provider) as remote,
    ):
        view = worker.run(config, WorkerConfig(binding, enabled=True, maximum=1), policy)
    assert (
        view["results"][0]["run_state"]
        == {"success": "succeeded", "budget": "waiting_budget", "cancel": "cancelled"}[mode]
    )
    assert remote.call_count == (mode != "budget")
    if mode == "success":
        assert result["source_version_id"] in delivery.prepare(config, binding, bid)["text"]


@pytest.mark.parametrize("text", ["查询本库：合成", "问题：查询本库：合成"])
def test_version_one_classification_is_not_reinterpreted(config, binding, text):
    from fiber_bundle import weixin_intents as intents
    from fiber_bundle.inputs import canonical
    from fiber_bundle.objects import digest
    from fiber_bundle.weixin_intent_text import classify

    bid = route(config, binding, [text], start=10)
    with inbox.connection(config, binding) as db:
        row = db.execute("SELECT * FROM events").fetchone()
        value = canonical(classify(json.loads(row["raw"]), version=1))
        db.execute(
            "UPDATE wx_event_intents SET intent_json=?,intent_sha256=?",
            (value, digest(value.encode())),
        )
        db.execute(
            "UPDATE wx_command_receipts SET result_json=?", (canonical({"intent_version": 1}),)
        )
        # Emulate the pre-version-2 freeze/plan, not a newer selected query.
        db.execute("DELETE FROM wx_batch_intents WHERE batch_id=?", (bid,))
        db.execute(
            "UPDATE wx_batches SET state='frozen',action=NULL,question=NULL WHERE id=?", (bid,)
        )
        old = intents.plan_locked(db, binding, bid)
        assert not old.get("query_existing")
        assert old["source_count"] == (text == "查询本库：合成")
        assert intents.read_locked(db, binding, bid) == old


def test_pre_guard_worker_snapshot_cannot_release_model_request_or_reserve(config, binding, policy):
    from fiber_bundle import model_gateway, weixin_note_queries

    published(config, binding, policy)
    _, rid = query(config, binding, policy)
    db = database.connect(config.state_root)
    ledger.start(config, db, rid, policy)
    db.close()
    # Previous workers compare plain note evidence and have no Weixin note-query guard.
    with (
        patch.object(
            weixin_note_queries, "snapshot", side_effect=lambda request, evidence: evidence
        ),
        patch.object(model_gateway, "complete") as remote,
        pytest.raises(IntegrityError, match="evidence changed"),
    ):
        execution.handle(config, policy, rid, {"type": "model", "id": "1", "body": {}})
    remote.assert_not_called()
    assert not main_rows(config, "model_calls")


def test_existing_query_uses_bounded_store_handoff_without_creating_receipts(
    config, binding, policy
):
    from fiber_bundle import weixin_store_dispatch as transfer

    query_grant(config, binding)
    bid = route(config, binding, ["查询本库：合成"], start=10)
    assert transfer.drain(config, binding, maximum=1) == 1
    assert handoff.get(config, binding, bid)["receipts"] == []
    assert transfer.drain(config, binding, maximum=1) == 0
    assert submission.dispatch(config, binding, policy, maximum=1)["processed"] == 1
    assert not main_rows(config, "receipts") and not main_rows(config, "model_calls")


def test_mixed_material_and_explicit_query_requires_scope_choice(config, binding):
    from fiber_bundle import weixin_intents as intents

    bid = route(config, binding, ["正文\n问题：查询本库：合成"], start=10)
    assert batches.get(config, binding, bid)["state"] == "frozen"
    with inbox.connection(config, binding) as db:
        intent = intents.read_locked(db, binding, bid)
        assert intent["reason"] == "conflicting_query_scope" and not intent.get("query_existing")
