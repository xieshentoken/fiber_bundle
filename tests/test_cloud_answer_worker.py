from dataclasses import replace
from unittest.mock import patch

import pytest
from test_cloud_answer_tasks import cloud_task as cloud_task
from test_cloud_answer_tasks import result
from test_local_query_tasks import local_task as local_task
from test_local_query_tasks import route as route
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner

from fiber_bundle import auth, database, execution_ledger
from fiber_bundle import cloud_answer_execution as execution
from fiber_bundle import cloud_answer_gateway as gateway
from fiber_bundle import cloud_answer_tasks as tasks
from fiber_bundle import cloud_answer_worker as worker
from fiber_bundle.answer_worker import worker_lock
from fiber_bundle.config import InvalidInput
from fiber_bundle.model_gateway import ModelFailure


def add(config, route, owner, original, name):
    db = database.connect(config.state_root)
    try:
        _, binding = tasks.get(db, owner[0], original)
        return tasks.submit(config, db, route, owner[0], binding["local_task_id"], name)["run_id"]
    finally:
        db.close()


def report(config, owner, run):
    db = database.connect(config.state_root)
    try:
        return tasks.status(db, owner[0], run)
    finally:
        db.close()


@pytest.mark.parametrize("allow,limit", [(False, 1), (True, 0), (False, 0)])
def test_disabled_before_any_io(allow, limit):
    with (
        patch("sqlite3.connect", side_effect=AssertionError("IO")),
        patch("subprocess.Popen", side_effect=AssertionError("process")),
    ):
        assert worker.dispatch(None, None, None, max_tasks=limit, allow_inference=allow) == {
            "state": "disabled",
            "processed": 0,
            "results": [],
        }


@pytest.mark.parametrize(
    "allow,limit", [(1, 1), (None, 1), (True, True), (True, -1), (True, 101), (True, 1.0)]
)
def test_limit_types_are_not_coerced(allow, limit):
    with pytest.raises(InvalidInput, match="limit"):
        worker.dispatch(None, None, None, max_tasks=limit, allow_inference=allow)


def test_fifo_batch_limit_and_no_replay(config, route, owner, cloud_task):
    second = add(config, route, owner, cloud_task, "edit-two")
    third = add(config, route, owner, cloud_task, "edit-three")
    with patch.object(gateway, "complete", return_value=result()) as provider:
        batch = worker.dispatch(config, route, owner[0], max_tasks=2, allow_inference=True)
        assert batch["state"] == "limit_reached" and batch["processed"] == 2
        assert [v["run_id"] for v in batch["results"]] == [cloud_task, second]
        assert provider.call_count == 2
        blocked = worker.dispatch(config, route, owner[0], max_tasks=1, allow_inference=True)
        assert blocked["state"] == "blocked" and provider.call_count == 2
    assert report(config, owner, third)["error_code"] == "cloud_budget_exceeded"
    assert (
        worker.dispatch(config, route, owner[0], max_tasks=1, allow_inference=True)["state"]
        == "idle"
    )


@pytest.mark.parametrize("mutation", ["foreign", "route", "policy", "prior_call", "attempt"])
def test_queue_never_claims_another_owner_route_or_attempt(
    config, route, owner, cloud_task, mutation
):
    skipped = add(config, route, owner, cloud_task, "skip")
    db = database.connect(config.state_root)
    if mutation == "foreign":
        other = auth.create_client(db, "foreign", False)["id"]
        db.execute("UPDATE execution_runs SET client_id=? WHERE id=?", (other, skipped))
        db.execute("UPDATE cloud_answer_tasks SET client_id=? WHERE run_id=?", (other, skipped))
    elif mutation == "route":
        db.execute(
            "UPDATE cloud_answer_tasks SET routing_digest='different' WHERE run_id=?", (skipped,)
        )
    elif mutation == "policy":
        db.execute("UPDATE execution_runs SET config_digest='different' WHERE id=?", (skipped,))
    elif mutation == "attempt":
        db.execute("UPDATE execution_runs SET attempts=1 WHERE id=?", (skipped,))
    else:
        db.execute(
            "INSERT INTO model_calls(id,run_id,model,price_revision,billing_day,currency,"
            "reserved_nusd,state,created_at) "
            "VALUES('prior',?,?,'synthetic','2000-01-01','USD',1,'uncertain',0)",
            (skipped, route.cloud_answer.model),
        )
    db.close()
    with patch.object(gateway, "complete", return_value=result()) as provider:
        batch = worker.dispatch(config, route, owner[0], max_tasks=2, allow_inference=True)
        assert batch["state"] == "idle" and batch["processed"] == 1
        assert provider.call_count == 1
    db = database.connect(config.state_root)
    assert (
        db.execute("SELECT state FROM execution_runs WHERE id=?", (skipped,)).fetchone()[0]
        == "queued"
    )
    db.close()


def test_cloud_batch_excludes_legacy_and_local_queues(config, route, owner, cloud_task, local_task):
    from fiber_bundle import local_query_tasks

    db = database.connect(config.state_root)
    local = local_query_tasks.submit(
        config, db, route, owner[0], replace(local_task[1], submission_id="local-wait")
    )["task_id"]
    source = db.execute("SELECT * FROM execution_runs WHERE id=?", (cloud_task,)).fetchone()
    old = execution_ledger.create_run(
        config,
        db,
        route.cloud_answer,
        owner[0],
        source["vault_id"],
        source["version_id"],
        answer=replace(local_task[1], submission_id="old-wait"),
    )
    db.close()
    with patch.object(gateway, "complete", return_value=result()):
        assert (
            worker.dispatch(config, route, owner[0], max_tasks=2, allow_inference=True)["processed"]
            == 1
        )
    db = database.connect(config.state_root)
    assert (
        db.execute("SELECT state FROM local_query_tasks WHERE id=?", (local,)).fetchone()[0]
        == "queued"
    )
    assert (
        db.execute("SELECT state FROM execution_runs WHERE id=?", (old,)).fetchone()[0] == "queued"
    )
    db.close()


@pytest.mark.parametrize("mode", ["failure", "cancel", "revoke", "source"])
def test_first_failed_or_cancelled_run_stops_remainder(config, route, owner, cloud_task, mode):
    second = add(config, route, owner, cloud_task, "waiting")

    def provider(*args, **kwargs):
        if mode == "failure":
            raise ModelFailure("cloud_timeout")
        if mode == "cancel":
            execution.cancel(config, owner[0], cloud_task)
        else:
            db = database.connect(config.state_root)
            if mode == "revoke":
                auth.revoke_client(db, owner[0])
            else:
                db.execute("DELETE FROM session_refs")
            db.close()
        return result()

    with patch.object(gateway, "complete", side_effect=provider) as calls:
        batch = worker.dispatch(config, route, owner[0], max_tasks=2, allow_inference=True)
        assert batch["state"] == "stopped" and batch["processed"] == 1 and calls.call_count == 1
    db = database.connect(config.state_root)
    assert (
        db.execute("SELECT state FROM execution_runs WHERE id=?", (second,)).fetchone()[0]
        == "queued"
    )
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 1
    db.close()


def test_preflight_and_shared_mutex_block_without_call(config, route, owner, cloud_task):
    with worker_lock(config), pytest.raises(InvalidInput, match="active"):
        worker.dispatch(config, route, owner[0], max_tasks=1, allow_inference=True)
    db = database.connect(config.state_root)
    db.execute("DELETE FROM session_refs")
    db.close()
    with patch.object(gateway, "complete", side_effect=AssertionError("provider")):
        assert (
            worker.dispatch(config, route, owner[0], max_tasks=1, allow_inference=True)["state"]
            == "blocked"
        )
    assert report(config, owner, cloud_task)["cloud_call"] is None


def test_missing_or_revoked_client_cannot_dispatch_even_empty_queue(
    config, route, owner, cloud_task
):
    with pytest.raises(auth.Denied):
        worker.dispatch(config, route, "foreign", max_tasks=1, allow_inference=True)
    db = database.connect(config.state_root)
    auth.revoke_client(db, owner[0])
    db.close()
    with pytest.raises(auth.Denied):
        worker.dispatch(config, route, owner[0], max_tasks=1, allow_inference=True)
