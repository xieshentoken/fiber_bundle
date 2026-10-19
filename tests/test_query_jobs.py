import json
import subprocess
from unittest.mock import patch

import pytest
from test_local_query_tasks import body
from test_local_query_tasks import local_task as local_task
from test_local_query_tasks import route as route
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner

from fiber_bundle import (
    cloud_answer_tasks,
    database,
    local_query_ledger,
    local_query_tasks,
    query_job_control,
    query_job_launch,
    query_job_runner,
    query_jobs,
    query_policy_store,
)
from fiber_bundle.config import InvalidInput
from fiber_bundle.executor_container import run_lock


@pytest.fixture
def job(config, route, owner, local_task):
    query_policy_store.save(
        config,
        json.loads(
            json.dumps(
                {
                    "schema": "query-routing-preparation/v1",
                    "enabled": False,
                    **__import__("dataclasses").asdict(route),
                }
            )
        ),
        None,
    )
    db = database.connect(config.state_root)
    value = query_jobs.submit(config, db, route, owner[0], "local", local_task[0], True)
    db.close()
    return value


def running(config, job):
    db = database.connect(config.state_root)
    db.execute("UPDATE query_jobs SET state='running' WHERE id=?", (job["id"],))
    db.close()


def test_versions_private_and_cas_no_secrets(config, store, routing):
    saved = query_policy_store.save(config, routing, None)
    assert saved["value"] == routing and not saved["inference_enabled"]
    digest = saved["configuration_sha256"]
    assert query_policy_store.version_path(config, digest).stat().st_mode & 0o777 == 0o600
    routing["local_query"]["model"] = "changed"
    with pytest.raises(InvalidInput, match="revision_conflict"):
        query_policy_store.save(config, routing, None)
    updated = query_policy_store.save(config, routing, digest)
    assert updated["configuration_sha256"] != digest
    assert query_policy_store.version(config, digest).local_query.model != "changed"
    assert not (config.project_root / "unread-local-secret").exists()


def test_same_admission_idempotent_conflict_cancel_blocks_cloud(
    config, route, owner, local_task, job
):
    db = database.connect(config.state_root)
    assert (
        query_jobs.submit(config, db, route, owner[0], "local", local_task[0], True)["id"]
        == job["id"]
    )
    with pytest.raises(InvalidInput, match="conflict"):
        query_jobs.submit(config, db, route, owner[0], "local", local_task[0], False)
    assert query_jobs.cancel(config, db, owner[0], job["id"])["state"] == "cancelled"
    assert local_query_tasks.get(db, owner[0], local_task[0])["state"] == "cancelled"
    db.close()
    with patch.object(
        query_job_runner.local_query_execution, "execute", side_effect=AssertionError("provider")
    ):
        assert query_job_runner.run(config, job["id"])["state"] == "not_run"


def test_local_failure_never_creates_cloud(config, job):
    running(config, job)
    with (
        patch.object(
            query_job_runner.local_query_execution,
            "execute",
            return_value={"state": "failed", "error_code": "local_timeout"},
        ),
        patch.object(
            query_job_runner.cloud_answer_execution, "execute", side_effect=AssertionError("cloud")
        ),
    ):
        assert query_job_runner.run(config, job["id"]) == {
            "state": "failed",
            "error_code": "local_timeout",
        }
    db = database.connect(config.state_root)
    assert db.execute("SELECT count(*) FROM cloud_answer_tasks").fetchone()[0] == 0
    db.close()


def test_finite_local_cloud_chain_uses_validated_local_result(
    config, route, owner, local_task, job
):
    running(config, job)

    def local(*args):
        db = database.connect(config.state_root)
        owned = local_query_ledger.claim(config, db, route, owner[0], local_task[0])
        result = local_query_ledger.finish(config, db, route, owned, body(route, owned))
        db.close()
        return result

    def cloud(config, route, cid, run_id, **kwargs):
        db = database.connect(config.state_root)
        owned = cloud_answer_tasks.claim(config, db, route, cid, run_id)
        assert "RAW-EVENT-SECRET" not in json.dumps(owned.answer)
        result = cloud_answer_tasks.finish(
            config,
            db,
            route,
            owned,
            {
                "answer": json.dumps(
                    {
                        "text": "synthetic cloud draft",
                        "quotations": json.loads(owned.answer["answer"])["quotations"],
                    }
                ),
                "usage": {"prompt_tokens": 20, "completion_tokens": 10},
            },
        )
        db.close()
        return result

    with (
        patch.object(query_job_runner.local_query_execution, "execute", side_effect=local),
        patch.object(query_job_runner.cloud_answer_execution, "execute", side_effect=cloud),
    ):
        assert query_job_runner.run(config, job["id"])["state"] == "succeeded"
        assert query_job_runner.run(config, job["id"])["state"] == "not_run"
    db = database.connect(config.state_root)
    cloud_id = query_jobs.view(db, owner[0], job["id"])["cloud_run_id"]
    assert (
        cloud_answer_tasks.deliver(config, db, owner[0], cloud_id)["cloud_answer"]["text"]
        == "synthetic cloud draft\n\n[1] 100 psi session evidence"
    )
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM local_query_calls").fetchone()[0] == 1
    db.close()


def test_launch_admission_commits_before_child_and_never_relaunches(config, owner, job):
    class Process:
        def wait(self):
            return 0

    def start(*args, **kwargs):
        db = database.connect(config.state_root)
        assert query_jobs.view(db, owner[0], job["id"])["state"] == "running"
        db.close()
        assert kwargs["start_new_session"] and kwargs["stdout"] == subprocess.DEVNULL
        assert "TEST_OPENCODEGO_API_KEY" not in kwargs["env"]
        return Process()

    with patch.object(query_job_launch.subprocess, "Popen", side_effect=start) as provider:
        query_job_launch.launch(config, owner[0], job["id"])
        query_job_launch.launch(config, owner[0], job["id"])
        assert provider.call_count == 1
    assert (config.state_root / "query-runner-config.json").stat().st_mode & 0o777 == 0o600


def test_failed_spawn_is_not_replayed(config, owner, job):
    with patch.object(
        query_job_launch.subprocess, "Popen", side_effect=OSError("synthetic")
    ) as provider:
        with pytest.raises(OSError):
            query_job_launch.launch(config, owner[0], job["id"])
        query_job_launch.launch(config, owner[0], job["id"])
        assert provider.call_count == 1
    db = database.connect(config.state_root)
    assert query_jobs.view(db, owner[0], job["id"])["state"] == "interrupted"
    db.close()


def test_recovery_refuses_live_owner_then_retains_uncertain_call(
    config, route, owner, local_task, job
):
    running(config, job)
    db = database.connect(config.state_root)
    local_query_ledger.claim(config, db, route, owner[0], local_task[0])
    with run_lock(config, job["id"]), pytest.raises(InvalidInput):
        query_job_control.recover(config, db, owner[0], job["id"])
    with patch.object(query_job_control.local_query_execution, "stop_container", return_value=True):
        assert query_job_control.recover(config, db, owner[0], job["id"])["state"] == "interrupted"
    assert (
        local_query_tasks.status(config, db, owner[0], local_task[0])["local_call"]["state"]
        == "uncertain"
    )
    db.close()


def test_interruption_keeps_accounting_and_suppresses_cloud(config, route, owner, local_task, job):
    running(config, job)

    def interrupted(*args):
        db = database.connect(config.state_root)
        local_query_ledger.claim(config, db, route, owner[0], local_task[0])
        db.close()
        raise KeyboardInterrupt()

    with (
        patch.object(query_job_runner.local_query_execution, "execute", side_effect=interrupted),
        pytest.raises(KeyboardInterrupt),
    ):
        query_job_runner.run(config, job["id"])
    db = database.connect(config.state_root)
    assert query_jobs.view(db, owner[0], job["id"])["state"] == "interrupted"
    assert (
        local_query_tasks.status(config, db, owner[0], local_task[0])["local_call"]["state"]
        == "uncertain"
    )
    assert db.execute("SELECT count(*) FROM cloud_answer_tasks").fetchone()[0] == 0
    db.close()


def test_real_finite_child_rejects_missing_image_without_reading_credentials(config, owner, job):
    query_job_launch.launch(config, owner[0], job["id"])
    import time

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        db = database.connect(config.state_root)
        value = query_jobs.view(db, owner[0], job["id"])
        db.close()
        if value["state"] != "running":
            break
        time.sleep(0.05)
    assert value["state"] == "failed" and not (config.project_root / "unread-local-secret").exists()
