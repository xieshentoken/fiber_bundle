"""Synthetic Feishu phone command → evidence task → guarded protocol acknowledgement."""

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from test_answer_tasks import invoke
from test_execution import policy as policy
from test_feishu_ingress import feishu as feishu
from test_feishu_ingress import payload
from test_weixin_inbox import binding as binding

from fiber_bundle import (
    answer_tasks,
    auth,
    database,
    delivery_reconciliation,
    execution_ledger,
    feishu_budgets,
    feishu_delivery,
    feishu_queries,
    feishu_receiver,
    weixin_batches,
    weixin_confirmations,
    weixin_inbox,
    weixin_store,
    weixin_task_dispatch,
)
from fiber_bundle.config import InvalidInput
from fiber_bundle.objects import digest


def ready(config, feishu, policy, text="查询本库：青松", *, store=False):
    from datetime import UTC, datetime, timedelta

    db = database.connect(config.state_root)
    auth.set_grant(db, feishu.client_id, feishu.vault_id, query=True, original=True, add=True)
    db.close()
    config.vault_path(dict(config.vaults)["personal"]).joinpath("literal.md").write_text(
        "青松\n测试压力：2.4 MPa\n"
    )
    event = payload(body={"text": text})
    feishu_receiver.capture(config, feishu, event, commands=True)
    feishu_receiver.capture(config, feishu, event, commands=True)
    feishu_receiver.advance(config, feishu, intent_routing=True)
    bid = weixin_batches.advance(config, feishu, now=datetime.now(UTC) + timedelta(seconds=5))[0][
        "batch_id"
    ]
    weixin_confirmations.tick(config, feishu, intent_routing=True)
    if store:
        weixin_store.transfer(config, feishu, bid)
    grant = str(uuid4())
    feishu_budgets.authorize(config, feishu, grant, 0, 0, 1, 1)
    assert feishu_queries.submit_ready(config, feishu, policy, grant) == 1
    assert feishu_queries.submit_ready(config, feishu, policy, grant) == 0
    with weixin_inbox.connection(config, feishu) as journal:
        rid = journal.execute(
            "SELECT run_id FROM wx_task_bindings WHERE batch_id=?", (bid,)
        ).fetchone()[0]
    return bid, rid, grant


def finish(config, feishu, policy, rid, grant):
    value = weixin_task_dispatch._claim(
        config,
        feishu,
        policy,
        lambda db, run: feishu_queries.reserve(config, feishu, grant, db, run),
    )
    assert value["run_id"] == rid and value["dispatch_phase"] == "claimed"
    db = database.connect(config.state_root)
    execution_ledger.start(config, db, rid, policy)
    db.close()
    invoke(config, policy, rid)
    db = database.connect(config.state_root)
    execution_ledger.transition(db, rid, "succeeded")
    db.close()
    weixin_task_dispatch._settle(config, feishu, rid, uncertain=False)


def test_explicit_note_query_quotes_and_once_protocol_confirmation(config, feishu, policy):
    policy = replace(policy, max_calls=1, max_attempts=1)
    bid, rid, grant = ready(config, feishu, policy)
    assert weixin_store.get(config, feishu, bid)["receipts"] == []
    finish(config, feishu, policy, rid, grant)
    db = database.connect(config.state_root)
    result = answer_tasks.deliver(config, db, feishu.client_id, rid)
    assert any("青松" in q["quote"] for q in result["quotations"])
    db.close()
    sent = []

    def send(target, text, guard):
        guard()
        sent.append((target, text))
        return {"category": "confirmed", "message_id_sha256": digest(b"synthetic-ack")}

    assert asyncio.run(feishu_delivery.deliver(config, feishu, bid, grant, send))["state"] == "sent"
    assert asyncio.run(feishu_delivery.deliver(config, feishu, bid, grant, send))["state"] == "sent"
    assert len(sent) == 1 and sent[0][0] == feishu.owner_id and "原文片段" in sent[0][1]
    feishu_budgets.authorize(config, feishu, grant, 0, 0, 1, 1)
    assert feishu_budgets.remaining(config, feishu, grant)["model"] == 0
    assert feishu_budgets.remaining(config, feishu, grant)["outbound"] == 0


@pytest.mark.parametrize("failure", ["missing_response", "negative_ack", "transport_exception"])
def test_unknown_no_retry_and_user_feedback_is_separate(config, feishu, policy, failure):
    policy = replace(policy, max_calls=1, max_attempts=1)
    bid, rid, grant = ready(config, feishu, policy)
    finish(config, feishu, policy, rid, grant)
    calls = []

    def send(target, text, guard):
        guard()
        calls.append(target)
        return {"category": failure}

    value = asyncio.run(feishu_delivery.deliver(config, feishu, bid, grant, send))
    assert value == {"state": "unknown", "category": failure}
    with pytest.raises(InvalidInput, match="unresolved"):
        asyncio.run(feishu_delivery.deliver(config, feishu, bid, grant, send))
    assert len(calls) == 1
    assert (
        delivery_reconciliation.record(config, "wx_task_deliveries", bid, "received")["state"]
        == "unknown"
    )
    row = delivery_reconciliation.listing(config)[0]
    assert row["user_feedback"] == "received" and row["protocol_ack"] == "unknown"
    with pytest.raises(InvalidInput, match="unresolved"):
        asyncio.run(feishu_delivery.deliver(config, feishu, bid, grant, send))


def test_consultation_excludes_question_and_cancel_revocation_fail_closed(config, feishu, policy):
    policy = replace(policy, max_calls=1, max_attempts=1)
    bid, rid, grant = ready(
        config, feishu, policy, "资料：青松压力2.4 MPa\n问题：青松是什么？", store=True
    )
    parent = weixin_store.get(config, feishu, bid)["receipts"][0]
    db = database.connect(config.state_root)
    assets = db.execute(
        "SELECT va.asset_id FROM version_assets va WHERE version_id=? AND role='body'",
        (parent["source_version_id"],),
    ).fetchall()
    from fiber_bundle.store import Store

    body = Store(config).read_asset(assets[0][0], vault="personal")
    assert b"2.4 MPa" in body and "问题".encode() not in body
    db.close()
    weixin_batches.choose(config, feishu, bid, "cancel")
    with pytest.raises(auth.Denied):
        db = database.connect(config.state_root)
        try:
            execution_ledger.authorize(config, db, execution_ledger.get_run(db, rid))
        finally:
            db.close()
    assert feishu_budgets.remaining(config, feishu, grant)["model"] == 1


def test_default_off_preserves_explicit_vault_and_send_gate_rechecks_query(config, feishu, policy):
    policy = replace(policy, max_calls=1, max_attempts=1)
    bid, rid, grant = ready(config, feishu, policy)
    db = database.connect(config.state_root)
    auth.set_grant(db, feishu.client_id, feishu.vault_id, query=True, original=False, add=True)
    db.execute("UPDATE vaults SET default_query=0 WHERE id=?", (feishu.vault_id,))
    db.close()
    finish(config, feishu, policy, rid, grant)
    effects = []

    def send(target, text, guard):
        db = database.connect(config.state_root)
        auth.set_grant(db, feishu.client_id, feishu.vault_id, query=False, original=False, add=True)
        db.close()
        guard()
        effects.append(text)
        return {"category": "confirmed", "message_id_sha256": digest(b"must-not-ack")}

    value = asyncio.run(feishu_delivery.deliver(config, feishu, bid, grant, send))
    assert value == {"state": "unknown", "category": "guard_rejected"}
    assert effects == [] and feishu_budgets.remaining(config, feishu, grant)["outbound"] == 0


def test_no_evidence_and_zero_model_budget_never_widen_scope(config, feishu, policy):
    policy = replace(policy, max_calls=1, max_attempts=1)
    bid, rid, grant = ready(config, feishu, policy, "查询本库：不存在的资料")
    other = str(uuid4())
    feishu_budgets.authorize(config, feishu, other, 0, 0)
    claim = weixin_task_dispatch._claim(
        config,
        feishu,
        policy,
        lambda db, run: feishu_queries.reserve(config, feishu, other, db, run),
    )
    assert claim["error_code"] == "model_quota_exhausted"
    db = database.connect(config.state_root)
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    db.close()
    finish(config, feishu, policy, rid, grant)
    db = database.connect(config.state_root)
    result = answer_tasks.deliver(config, db, feishu.client_id, rid)
    assert result["quotations"] == [] and result["sources"] == []
    db.close()


def test_ingress_add_revocation_blocks_claim_without_consuming_model(config, feishu, policy):
    policy = replace(policy, max_calls=1, max_attempts=1)
    _, _, grant = ready(config, feishu, policy)
    db = database.connect(config.state_root)
    auth.set_grant(db, feishu.client_id, feishu.vault_id, query=True, original=False, add=False)
    db.close()
    with pytest.raises(auth.Denied):
        weixin_task_dispatch._claim(
            config,
            feishu,
            policy,
            lambda db, run: feishu_queries.reserve(config, feishu, grant, db, run),
        )
    db = database.connect(config.state_root)
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    db.close()
