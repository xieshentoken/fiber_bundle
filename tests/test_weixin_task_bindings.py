"""Durable queue intent and cancellation gates; all inputs are synthetic."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from test_execution import policy as policy
from test_weixin_inbox import binding as binding
from test_weixin_store import main_rows
from test_weixin_task_inputs import CAPTURE, query_grant, saved

from fiber_bundle import answer_tasks, article_bindings, auth, database, execution_ledger
from fiber_bundle import weixin_batches as batches
from fiber_bundle import weixin_inbox as inbox
from fiber_bundle import weixin_task_bindings as bindings
from fiber_bundle import weixin_task_inputs as inputs
from fiber_bundle.config import InvalidInput


def setup(config, binding, article=False):
    query_grant(config, binding)
    return saved(
        config,
        binding,
        "save_one" if article else "consult",
        ["https://x.com/a/status/1" if article else "合成正文"],
    )[0]


@pytest.mark.parametrize("article", [False, True])
def test_parallel_replay_and_policy_conflict(config, binding, policy, article):
    bid = setup(config, binding, article)
    kwargs = {"capture_policy": CAPTURE} if article else {}

    def submit(_):
        return bindings.submit(config, binding, bid, policy, **kwargs)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert results[0] == results[1]
    assert results[0]["state"] == "queued"
    assert len(main_rows(config, "execution_runs")) == 1
    assert not main_rows(config, "model_calls")
    with pytest.raises(InvalidInput, match="conflicts"):
        bindings.submit(config, binding, bid, replace(policy, max_attempts=3), **kwargs)


@pytest.mark.parametrize("committed", [False, True])
def test_crash_reuses_durable_run_id(config, binding, policy, monkeypatch, committed):
    bid = setup(config, binding)
    original = execution_ledger.create_run

    def crash(*args, **kwargs):
        if committed:
            original(*args, **kwargs)
        raise RuntimeError("synthetic interruption")

    monkeypatch.setattr(execution_ledger, "create_run", crash)
    with pytest.raises(RuntimeError, match="interruption"):
        bindings.submit(config, binding, bid, policy)
    with inbox.connection(config, binding) as journal:
        intended = journal.execute("SELECT run_id FROM wx_task_bindings").fetchone()[0]
    assert len(main_rows(config, "execution_runs")) == int(committed)
    monkeypatch.setattr(execution_ledger, "create_run", original)
    assert bindings.submit(config, binding, bid, policy)["run_id"] == intended
    assert len(main_rows(config, "execution_runs")) == 1


def test_unbound_native_submission_rolls_back(config, binding, policy):
    bid = setup(config, binding)
    data = inputs.prepare_answer(config, binding, bid)
    db = database.connect(config.state_root)
    try:
        vault, version = answer_tasks.scope(config, db, binding.client_id, data)
        with pytest.raises(auth.Denied):
            execution_ledger.create_run(
                config, db, policy, binding.client_id, vault, version, answer=data
            )
    finally:
        db.close()
    assert not main_rows(config, "execution_runs")
    assert not main_rows(config, "answer_tasks")


@pytest.mark.parametrize("article", [False, True])
@pytest.mark.parametrize("started", [False, True])
def test_cancel_blocks_execution_and_delivery(config, binding, policy, article, started):
    bid = setup(config, binding, article)
    kwargs = {"capture_policy": CAPTURE} if article else {}
    run_id = bindings.submit(config, binding, bid, policy, **kwargs)["run_id"]
    db = database.connect(config.state_root)
    try:
        if started:
            execution_ledger.start(config, db, run_id, policy)
        batches.choose(config, binding, bid, "cancel")
        with pytest.raises(auth.Denied):
            execution_ledger.start(config, db, run_id, policy)
        with pytest.raises(auth.Denied):
            execution_ledger.reserve(config, db, run_id, policy)
        run = execution_ledger.get_run(db, run_id)
        if article:
            with pytest.raises(auth.Denied):
                article_bindings.authorize(config, db, run, article_bindings.get(db, run_id))
        else:
            with pytest.raises(auth.Denied):
                answer_tasks.deliver(config, db, binding.client_id, run_id)
            with pytest.raises(auth.Denied):
                answer_tasks.accept(config, db, run, {})
        assert run["state"] == ("running" if started else "queued")
    finally:
        db.close()
    assert not main_rows(config, "model_calls")
    with pytest.raises(InvalidInput):
        bindings.submit(config, binding, bid, policy, **kwargs)


@pytest.mark.parametrize(
    "mutation",
    [
        "DELETE FROM wx_task_bindings",
        "UPDATE wx_task_bindings SET request_json='{}'",
        "UPDATE wx_store_handoffs SET plan_json='{}'",
    ],
)
def test_missing_or_changed_binding_denied(config, binding, policy, mutation):
    bid = setup(config, binding)
    run_id = bindings.submit(config, binding, bid, policy)["run_id"]
    with inbox.connection(config, binding) as journal:
        journal.execute(mutation)
        journal.commit()
    db = database.connect(config.state_root)
    try:
        with pytest.raises(auth.Denied):
            execution_ledger.start(config, db, run_id, policy)
    finally:
        db.close()


def test_main_transaction_failure_keeps_recoverable_intent(config, binding, policy, monkeypatch):
    bid = setup(config, binding)
    original = execution_ledger.authorize

    def reject(*args, **kwargs):
        raise auth.Denied(404)

    monkeypatch.setattr(execution_ledger, "authorize", reject)
    with pytest.raises(auth.Denied):
        bindings.submit(config, binding, bid, policy)
    assert not main_rows(config, "execution_runs")
    assert not main_rows(config, "answer_tasks")
    with inbox.connection(config, binding) as journal:
        intended = journal.execute("SELECT run_id FROM wx_task_bindings").fetchone()[0]
    monkeypatch.setattr(execution_ledger, "authorize", original)
    assert bindings.submit(config, binding, bid, policy)["run_id"] == intended


def test_cancel_before_submission_and_capture_policy_conflict(config, binding, policy):
    bid = setup(config, binding, True)
    bindings.submit(config, binding, bid, policy, capture_policy=CAPTURE)
    with pytest.raises(InvalidInput, match="conflicts"):
        bindings.submit(
            config, binding, bid, policy, capture_policy=replace(CAPTURE, timeout_seconds=61)
        )
    batches.choose(config, binding, bid, "cancel")
    with pytest.raises(InvalidInput):
        bindings.submit(config, binding, bid, policy, capture_policy=CAPTURE)
    assert len(main_rows(config, "execution_runs")) == 1
    assert not main_rows(config, "model_calls")
