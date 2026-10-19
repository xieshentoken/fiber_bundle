import asyncio
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from test_local_query_tasks import local_task as local_task
from test_local_query_tasks import route as route
from test_media_jobs import media_scope as media_scope
from test_query_jobs import job as job
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner

from fiber_bundle import auth, backend_lifecycle, database, execution_scheduler, query_jobs
from fiber_bundle.api import create_app
from fiber_bundle.config import InvalidInput


def wait_for(check):
    deadline = time.monotonic() + 3
    while not check():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_queue_interactive_priority_and_serial_execution(config, store):
    order = []

    def work(priority):
        with execution_scheduler.admit(config, priority, timeout=3):
            order.append(priority)
            time.sleep(0.02)

    with ThreadPoolExecutor(2) as pool:
        with execution_scheduler.admit(config, "heavy"):
            heavy = pool.submit(work, "heavy")
            wait_for(lambda: execution_scheduler.snapshot(config)["heavy_waiting"] == 1)
            interaction = pool.submit(work, "interactive")
            wait_for(lambda: execution_scheduler.snapshot(config)["interactive_waiting"] == 1)
            assert order == []
        heavy.result()
        interaction.result()
    assert order == ["interactive", "heavy"]
    assert execution_scheduler.snapshot(config) == {"interactive_waiting": 0, "heavy_waiting": 0}


def test_queue_timeout_cleans_admission_and_retains_slot_owner(config, store):
    with execution_scheduler.admit(config, "heavy"):
        with pytest.raises(InvalidInput, match="queue_timeout"):
            with execution_scheduler.admit(config, "interactive", timeout=0.05):
                pytest.fail("concurrent execution")
    assert not list((config.state_root / "execution-queue").glob("*.ticket"))


def test_released_crash_ticket_only_is_recovered(config, store):
    root = config.state_root / "execution-queue"
    root.mkdir()
    (root / "c677db5e-33c1-4071-b782-f2bdc063ab8b.ticket").write_text(
        json.dumps({"priority": "interactive", "time": 1})
    )
    with execution_scheduler.admit(config, "heavy", timeout=0.1):
        pass
    db = database.connect(config.state_root)
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    db.close()


def test_backend_lock_reuse_live_status_and_stale_status(config, store):
    control = backend_lifecycle.BackendControl(config, "http://127.0.0.1:8765")
    with backend_lifecycle.process_lock(config):
        control.record("running")
        assert backend_lifecycle.observe(config)["instance_id"] == control.instance_id
        with pytest.raises(InvalidInput, match="already_running"):
            with backend_lifecycle.process_lock(config):
                pass
    assert backend_lifecycle.observe(config)["state"] == "stopped"
    assert (config.state_root / "backend.json").stat().st_mode & 0o777 == 0o600


def test_backend_api_stop_cookie_csrf_exact_instance(config, store):
    auth.set_password(config, "synthetic-m12-password")
    control = backend_lifecycle.BackendControl(config, "http://127.0.0.1:8765")
    control.record("running")
    with TestClient(
        create_app(config, lifecycle=control), base_url=control.origin, client=("127.0.0.1", 49000)
    ) as http:
        assert http.get("/admin/backend").status_code == 401
        login = http.post(
            "/admin/login",
            json={"password": "synthetic-m12-password"},
            headers={"Origin": control.origin},
        )
        http.headers["Origin"] = control.origin
        assert (
            http.post("/admin/backend/stop", json={"instance_id": control.instance_id}).status_code
            == 403
        )
        http.headers["X-CSRF-Token"] = login.json()["csrf"]
        assert http.post("/admin/backend/stop", json={"instance_id": "wrong"}).status_code == 422
        assert not control.stop_event.is_set()
        assert (
            http.post("/admin/backend/stop", json={"instance_id": control.instance_id}).json()[
                "state"
            ]
            == "stopping"
        )
        with pytest.raises(InvalidInput, match="stopping"):
            control.permit()


def test_unmanaged_api_cannot_stop_process(http):
    assert http.get("/admin/backend").json()["managed"] is False
    assert http.post("/admin/backend/stop", json={"instance_id": "fake"}).status_code == 422


def test_backend_stop_cancels_admitted_queries(config, job):
    control = backend_lifecycle.BackendControl(config, "http://127.0.0.1:8765")
    db = database.connect(config.state_root)
    control.stop(db, control.instance_id)
    assert query_jobs.view(db, job["client_id"], job["id"])["state"] == "cancelled"
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    db.close()


def test_restart_only_resumes_reserved_without_attempt_never_running(config, job):
    def launched(config, cid, jid):
        db = database.connect(config.state_root)
        db.execute("UPDATE query_jobs SET state='running' WHERE id=?", (jid,))
        db.close()

    with patch.object(backend_lifecycle.query_job_launch, "launch", side_effect=launched) as launch:
        assert backend_lifecycle.resume_safe(config) == {"resumed": 1, "expert_interrupted": 0}
        assert backend_lifecycle.resume_safe(config) == {"resumed": 0, "expert_interrupted": 0}
        assert launch.call_count == 1


def test_restart_cloud_reserved_uses_execution_runs_and_keeps_fixed_policy(
    config, route, owner, local_task
):
    from dataclasses import asdict

    from test_local_query_tasks import body

    from fiber_bundle import cloud_answer_tasks, local_query_ledger, query_policy_store

    db = database.connect(config.state_root)
    owned = local_query_ledger.claim(config, db, route, owner[0], local_task[0])
    local_query_ledger.finish(config, db, route, owned, body(route, owned))
    query_policy_store.save(
        config,
        json.loads(
            json.dumps(
                {"schema": "query-routing-preparation/v1", "enabled": False, **asdict(route)}
            )
        ),
        None,
    )
    cloud = cloud_answer_tasks.submit(config, db, route, owner[0], local_task[0], "m12-cloud")
    job = query_jobs.submit(config, db, route, owner[0], "cloud", cloud["run_id"])
    db.close()
    with patch.object(backend_lifecycle.query_job_launch, "launch") as launched:
        assert backend_lifecycle.resume_safe(config) == {"resumed": 1, "expert_interrupted": 0}
        assert launched.call_args.args[2] == job["id"]


def test_restart_does_not_resume_prior_local_attempt(config, route, owner, local_task, job):
    from fiber_bundle import local_query_ledger

    db = database.connect(config.state_root)
    local_query_ledger.claim(config, db, route, owner[0], local_task[0])
    db.close()
    with patch.object(backend_lifecycle.query_job_launch, "launch") as launched:
        assert backend_lifecycle.resume_safe(config) == {"resumed": 0, "expert_interrupted": 0}
        launched.assert_not_called()


def test_process_crash_releases_execution_slot_without_replaying_io(config, store):
    script = """
import os, sys
from pathlib import Path
from fiber_bundle.config import load_config
from fiber_bundle.execution_scheduler import admit
root = Path(sys.argv[1])
with admit(load_config(root / 'config.json', root), 'heavy'):
    os._exit(23)
"""
    result = subprocess.run([sys.executable, "-c", script, str(config.project_root)], timeout=5)
    assert result.returncode == 23
    with execution_scheduler.admit(config, "interactive", timeout=0.2):
        db = database.connect(config.state_root)
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
        db.close()


def test_resume_failure_keeps_graceful_stop_listener(config, store):
    from fiber_bundle import backend_cli
    from fiber_bundle.http_guard import Settings

    control = backend_lifecycle.BackendControl(config, Settings().origin)

    class Server:
        started = True
        should_exit = False

        def __init__(self, settings):
            pass

        async def serve(self):
            for _ in range(40):
                if control.recovery["state"] == "blocked":
                    control.stop_event.set()
                if self.should_exit:
                    return
                await asyncio.sleep(0.02)
            pytest.fail("resume failure disabled the stop listener")

    with (
        patch.object(backend_cli.uvicorn, "Server", Server),
        patch.object(
            backend_lifecycle, "resume_safe", side_effect=InvalidInput("reserved_job_conflict")
        ),
    ):
        asyncio.run(backend_cli.serve(config, Settings(), control))
    assert control.state == "stopped"
    assert control.recovery == {"state": "blocked", "error_type": "InvalidInput"}


def test_media_processing_waits_in_heavy_queue_and_rechecks_revocation(config, media_scope):
    from test_media_processing import RESOURCE, ready

    from fiber_bundle import media_processing

    job_id = ready(config, media_scope)
    cid = media_scope[0]["id"]
    media_processing.create(config, cid, job_id, RESOURCE)
    with patch.object(media_processing.worker, "execute") as worker, ThreadPoolExecutor(1) as pool:
        with execution_scheduler.admit(config, "interactive"):
            pending = pool.submit(media_processing.run, config, cid, job_id)
            wait_for(lambda: execution_scheduler.snapshot(config)["heavy_waiting"] == 1)
            worker.assert_not_called()
            db = database.connect(config.state_root)
            auth.revoke_client(db, cid)
            db.close()
        with pytest.raises(auth.Denied):
            pending.result()
        worker.assert_not_called()


def test_media_extraction_waits_and_does_not_start_after_revoke(config, media_scope):
    from test_media_jobs import create

    from fiber_bundle import media_jobs

    job_id = create(config, media_scope)
    cid = media_scope[0]["id"]
    with patch("fiber_bundle.media_worker.execute") as worker, ThreadPoolExecutor(1) as pool:
        with execution_scheduler.admit(config, "interactive"):
            pending = pool.submit(media_jobs.run, config, cid, job_id)
            wait_for(lambda: execution_scheduler.snapshot(config)["heavy_waiting"] == 1)
            worker.assert_not_called()
            db = database.connect(config.state_root)
            auth.revoke_client(db, cid)
            db.close()
        with pytest.raises(auth.Denied):
            pending.result()
        worker.assert_not_called()
