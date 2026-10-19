"""Task input compatibility without changing existing task authorization or running models."""

import json

import pytest
from test_execution import policy as policy
from test_weixin_batches import at, capture
from test_weixin_inbox import binding as binding
from test_weixin_store import main_rows

from fiber_bundle import answer_tasks, article_bindings, auth, database, execution_ledger
from fiber_bundle import weixin_batches as batches
from fiber_bundle import weixin_confirmations as confirmations
from fiber_bundle import weixin_store as handoff
from fiber_bundle import weixin_task_bindings as bindings
from fiber_bundle import weixin_task_inputs as inputs
from fiber_bundle.article_worker import CapturePolicy
from fiber_bundle.articles import ArticleError
from fiber_bundle.config import InvalidInput
from fiber_bundle.store import Store

CAPTURE = CapturePolicy("sha256:" + "a" * 64)


def saved(config, binding, action, texts):
    confirmations.initialize(config, binding)
    for i, text in enumerate(texts):
        capture(config, binding, str(i), i, item_list=[{"type": 1, "text_item": {"text": text}}])
    confirmations.process(config, binding)
    bid = batches.advance(config, binding, now=at(len(texts) + 3))[0]["batch_id"]
    batches.choose(
        config, binding, bid, action, question="合成正文" if action == "consult" else None
    )
    return bid, handoff.transfer(config, binding, bid)


def query_grant(config, binding):
    db = database.connect(config.state_root)
    auth.set_grant(db, binding.client_id, binding.vault_id, add=True, query=True, original=False)
    db.close()


def test_article_input_compatible_with_existing_task_binding(config, binding, policy):
    bid, transfer = saved(config, binding, "save_one", ["https://mp.weixin.qq.com/s/synthetic"])
    prepared = inputs.prepare_article(config, binding, bid, CAPTURE)
    assert inputs.prepare_article(config, binding, bid, CAPTURE) == prepared
    assert len(main_rows(config, "receipts")) == 2
    assert not main_rows(config, "execution_runs")
    db = database.connect(config.state_root)
    try:
        run = bindings.submit(config, binding, bid, policy, capture_policy=CAPTURE)["run_id"]
        execution_ledger.authorize(config, db, execution_ledger.get_run(db, run))
        assert article_bindings.get(db, run)["source_url"] == "https://mp.weixin.qq.com/s/synthetic"
        assert execution_ledger.get_run(db, run)["state"] == "queued"
    finally:
        db.close()
    parent = transfer["receipts"][0]["job_id"]
    receipt = next(r for r in main_rows(config, "receipts") if r["job_id"] == prepared.receipt_id)
    raw = json.loads(Store(config).read_asset(receipt["raw_asset_id"], vault="personal"))
    assert raw["parent_receipt_id"] == parent
    assert b"synthetic-private-route" not in json.dumps(raw).encode()
    assert not main_rows(config, "model_calls")


def test_answer_input_binds_only_client_session_and_separate_question(config, binding, policy):
    bid, _ = saved(config, binding, "consult", ["合成正文第一份", "合成正文第二份"])
    with pytest.raises(auth.Denied):
        inputs.prepare_answer(config, binding, bid)
    assert len(main_rows(config, "receipts")) == 1
    query_grant(config, binding)
    prepared = inputs.prepare_answer(config, binding, bid)
    assert inputs.prepare_answer(config, binding, bid) == prepared
    assert prepared.question == "合成正文" and prepared.mode == "extractive"
    assert not main_rows(config, "answer_tasks")
    db = database.connect(config.state_root)
    try:
        run = bindings.submit(config, binding, bid, policy)["run_id"]
        task = answer_tasks.get(db, run)
        answer_tasks.authorize(config, db, execution_ledger.get_run(db, run), task)
        current = answer_tasks.current(config, db, execution_ledger.get_run(db, run), task)
        assert current["sources"]
        with pytest.raises(auth.Denied):
            answer_tasks.source(
                config, db, "other-client", prepared.receipt_id, prepared.session_id
            )
        auth.set_grant(
            db, binding.client_id, binding.vault_id, add=True, query=False, original=False
        )
        with pytest.raises(auth.Denied):
            answer_tasks.authorize(config, db, execution_ledger.get_run(db, run), task)
    finally:
        db.close()
    assert {v["retention"] for v in main_rows(config, "source_versions")} == {"session"}
    assert not main_rows(config, "model_calls")


@pytest.mark.parametrize(
    "body",
    [
        "正文 https://x.com/user/status/123",
        "https://x.com/a https://x.com/b",
        "http://x.com/a",
        "https://evil.invalid/a",
    ],
)
def test_nonliteral_or_unsupported_article_refused(config, binding, body):
    bid, _ = saved(config, binding, "save_one", [body])
    with pytest.raises((InvalidInput, ArticleError)):
        inputs.prepare_article(config, binding, bid, CAPTURE)
    assert len(main_rows(config, "receipts")) == 1


def test_multiple_receipts_require_explicit_parent(config, binding):
    bid, result = saved(
        config, binding, "save_separately", ["https://x.com/a/status/1", "https://x.com/b/status/2"]
    )
    with pytest.raises(InvalidInput, match="exact parent"):
        inputs.prepare_article(config, binding, bid, CAPTURE)
    with pytest.raises(InvalidInput, match="outside"):
        inputs.prepare_article(config, binding, bid, CAPTURE, parent_receipt_id="unrelated")
    first = inputs.prepare_article(
        config, binding, bid, CAPTURE, parent_receipt_id=result["receipts"][0]["job_id"]
    )
    second = inputs.prepare_article(
        config, binding, bid, CAPTURE, parent_receipt_id=result["receipts"][1]["job_id"]
    )
    assert first.receipt_id != second.receipt_id


def test_merged_multiple_bodies_not_silently_reduced_to_one_url(config, binding):
    bid, _ = saved(
        config, binding, "save_one", ["https://x.com/a/status/1", "https://x.com/b/status/2"]
    )
    with pytest.raises(InvalidInput, match="one literal"):
        inputs.prepare_article(config, binding, bid, CAPTURE)
    assert len(main_rows(config, "receipts")) == 1


def test_action_conflict_and_cancelled_batch_refused(config, binding):
    bid, _ = saved(config, binding, "save_one", ["https://x.com/a/status/1"])
    with pytest.raises(InvalidInput, match="conflicts"):
        inputs.prepare_answer(config, binding, bid)
    inputs.prepare_article(config, binding, bid, CAPTURE)
    batches.choose(config, binding, bid, "cancel")
    with pytest.raises(InvalidInput, match="active"):
        inputs.prepare_article(config, binding, bid, CAPTURE)
    assert len(main_rows(config, "receipts")) == 2
