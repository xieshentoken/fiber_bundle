"""Real Store handoff with synthetic sources, credentials and interrupted transactions."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_weixin_batches import at, capture
from test_weixin_confirmations import prompt as prompt
from test_weixin_inbox import binding as binding
from test_weixin_inbox import rows
from test_weixin_receiver import revoke

from fiber_bundle import auth, database
from fiber_bundle import weixin_batches as batches
from fiber_bundle import weixin_confirmations as confirmations
from fiber_bundle import weixin_inbox as inbox
from fiber_bundle import weixin_recovery as recovery
from fiber_bundle import weixin_store as handoff
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.store import Store


def main_rows(config, table):
    db = database.connect(config.state_root)
    try:
        return [dict(r) for r in db.execute(f"SELECT * FROM {table}")]
    finally:
        db.close()


@pytest.mark.parametrize(
    "action,count,retention",
    [
        ("save_one", 1, "knowledge"),
        ("save_separately", 2, "knowledge"),
        ("consult", 1, "session"),
    ],
)
def test_action_routing_and_exact_replay(config, binding, prompt, action, count, retention):
    batch_id = prompt[0]
    question = "仅作为用户问题" if action == "consult" else None
    batches.choose(config, binding, batch_id, action, question=question)
    result = handoff.transfer(config, binding, batch_id)
    assert len(result["receipts"]) == count and result["question"] == question
    assert not result["published"] and not result["dispatched"]
    assert handoff.transfer(config, binding, batch_id) == result
    assert handoff.get(config, binding, batch_id)["store_state"] == "stored"
    assert len(main_rows(config, "receipts")) == count
    assert len(main_rows(config, "source_versions")) == count
    assert {r["retention"] for r in main_rows(config, "source_versions")} == {retention}
    assert {r["owner_client_id"] for r in main_rows(config, "receipts")} == {binding.client_id}
    all_bytes = b"".join(
        Store(config).read_asset(r["id"], vault="personal") for r in main_rows(config, "assets")
    )
    assert b"synthetic-private-route" not in all_bytes
    if question:
        assert question.encode() not in all_bytes
    provenance = [
        json.loads(Store(config).read_asset(r["raw_asset_id"], vault="personal"))
        for r in main_rows(config, "receipts")
    ]
    assert sum(len(p["sources"]) for p in provenance) == 2
    assert {s["journal_job_id"] for p in provenance for s in p["sources"]} == {
        r["job_id"] for r in rows(config, "events")
    }
    assert main_rows(config, "execution_runs") == []
    assert main_rows(config, "answer_tasks") == []


@pytest.mark.parametrize("mode", ["unselected", "cancelled", "question", "revoked", "corrupt"])
def test_guard_failures_do_not_write_store(config, binding, prompt, mode):
    batch_id = prompt[0]
    if mode == "cancelled":
        batches.choose(config, binding, batch_id, "cancel")
    elif mode == "question":
        batches.choose(config, binding, batch_id, "consult")
    elif mode in {"revoked", "corrupt"}:
        batches.choose(config, binding, batch_id, "save_one")
        if mode == "revoked":
            revoke(config, binding)
        else:
            with inbox.connection(config, binding) as db:
                db.execute("UPDATE events SET raw_sha256='broken'")
    with pytest.raises((InvalidInput, IntegrityError, auth.Denied)):
        handoff.transfer(config, binding, batch_id)
    assert main_rows(config, "receipts") == []


def test_recovered_media_and_reference_text_have_distinct_provenance(config, binding):
    confirmations.initialize(config, binding)
    recovery.initialize(config, binding)
    capture(
        config,
        binding,
        "media",
        0,
        item_list=[
            {
                "type": 2,
                "image_item": {"aes_key": "synthetic-key-never-export"},
                "ref_msg": {"message_item": {"type": 1, "text_item": {"text": "引用正文"}}},
            }
        ],
    )
    confirmations.process(config, binding)
    batch = batches.advance(config, binding, now=at(4))[0]
    event = rows(config, "events")[0]
    with pytest.raises(InvalidInput):
        handoff.transfer(config, binding, batch["batch_id"])
    recovery.complete(
        config, binding, event["job_id"], event["raw_sha256"], {"0": b"synthetic-image"}
    )
    batches.choose(config, binding, batch["batch_id"], "save_one")
    handoff.transfer(config, binding, batch["batch_id"])
    receipt = main_rows(config, "receipts")[0]
    meta = json.loads(Store(config).read_asset(receipt["raw_asset_id"], vault="personal"))
    assert [i["item_key"] for i in meta["sources"][0]["items"]] == ["0", "0.ref"]
    assets = [
        Store(config).read_asset(r["id"], vault="personal") for r in main_rows(config, "assets")
    ]
    assert b"synthetic-image" in assets and "引用正文".encode() in assets
    assert b"synthetic-key-never-export" not in b"".join(assets)
    assert rows(config, "events")[0]["state"] == "incomplete"


@pytest.mark.parametrize("after_commit", [False, True])
def test_resume_after_partial_store_or_unrecorded_commit(
    config, binding, prompt, monkeypatch, after_commit
):
    batch_id = prompt[0]
    batches.choose(config, binding, batch_id, "save_separately")
    original = Store.ingest_locked
    calls = []

    def failing(self, *args, **kwargs):
        calls.append(1)
        if not after_commit and len(calls) == 2:
            raise OSError("synthetic second target failure")
        result = original(self, *args, **kwargs)
        if after_commit and len(calls) == 1:
            raise OSError("synthetic crash after commit")
        return result

    monkeypatch.setattr(Store, "ingest_locked", failing)
    with pytest.raises(OSError):
        handoff.transfer(config, binding, batch_id)
    assert handoff.get(config, binding, batch_id)["store_state"] == "prepared"
    assert len(main_rows(config, "receipts")) == 1
    monkeypatch.setattr(Store, "ingest_locked", original)
    result = handoff.transfer(config, binding, batch_id)
    assert len(result["receipts"]) == 2 and len(main_rows(config, "receipts")) == 2
    assert len(main_rows(config, "source_versions")) == 2


def test_cancel_after_partial_handoff_prevents_remaining_work(config, binding, prompt, monkeypatch):
    batch_id = prompt[0]
    batches.choose(config, binding, batch_id, "save_separately")
    original = Store.ingest_locked
    calls = []

    def failing(self, *args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("stop")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Store, "ingest_locked", failing)
    with pytest.raises(OSError):
        handoff.transfer(config, binding, batch_id)
    batches.choose(config, binding, batch_id, "cancel")
    with pytest.raises(InvalidInput):
        handoff.transfer(config, binding, batch_id)
    assert handoff.get(config, binding, batch_id)["batch_state"] == "cancelled"
    assert len(main_rows(config, "receipts")) == 1


def test_changed_scope_after_preparation_refused(config, binding, prompt, monkeypatch):
    batch_id = prompt[0]
    batches.choose(config, binding, batch_id, "save_one")
    result = handoff.transfer(config, binding, batch_id)
    with inbox.connection(config, binding) as db:
        db.execute("DELETE FROM wx_batch_members WHERE batch_id=? AND ordinal=1", (batch_id,))
    with pytest.raises(IntegrityError, match="plan changed"):
        handoff.transfer(config, binding, batch_id)
    assert len(main_rows(config, "receipts")) == len(result["receipts"])


def test_concurrent_handoffs_create_one_source(config, binding, prompt):
    batches.choose(config, binding, prompt[0], "save_one")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(handoff.transfer, config, binding, prompt[0]) for _ in range(2)]
        assert futures[0].result() == futures[1].result()
    assert len(main_rows(config, "receipts")) == 1
