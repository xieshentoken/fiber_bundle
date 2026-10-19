"""Real journal attempts, bounded execution and no-network crash reconciliation."""

import asyncio

import pytest
from test_weixin_inbox import binding as binding
from test_weixin_inbox import message, poll, rows

from fiber_bundle import auth, database
from fiber_bundle import weixin_inbox as inbox
from fiber_bundle import weixin_media_queue as queue
from fiber_bundle import weixin_media_worker as worker
from fiber_bundle import weixin_recovery as recovery
from fiber_bundle.config import InvalidInput


@pytest.fixture
def tasks(config, binding):
    queue.initialize(config, binding)
    item = {"type": 4, "file_item": {"media": {"full_url": "https://novac2c.cdn.weixin.qq.com/a"}}}
    job = inbox.receive(config, binding, "", poll(message(item_list=[item, item])))["events"][0][
        "job_id"
    ]
    return queue.enqueue(config, binding, job)


async def good(url):
    return b"synthetic bytes"


def test_bounded_fifo_and_enqueue_idempotency(config, binding, tasks):
    assert queue.enqueue(config, binding, tasks[0]["job_id"]) == tasks
    first = asyncio.run(worker.dispatch(config, binding, fetcher=good))
    assert first["state"] == "limit_reached" and first["attempts"][0]["id"] == tasks[0]["id"]
    assert queue.status(config, binding, tasks[1]["id"])["state"] == "queued"
    last = asyncio.run(worker.dispatch(config, binding, max_items=10, fetcher=good))
    assert last["state"] == "idle" and len(last["attempts"]) == 1
    assert len(queue.list_attempts(config, binding)["attempts"]) == 2
    assert queue.enqueue(config, binding, tasks[0]["job_id"]) == []


def test_fail_stop_and_explicit_retry_preserve_history(config, binding, tasks):
    calls = []

    async def bad(url):
        calls.append(1)
        raise OSError("private URL must not enter ledger")

    result = asyncio.run(worker.dispatch(config, binding, max_items=10, fetcher=bad))
    assert result["state"] == "stopped" and len(calls) == 1
    old = queue.status(config, binding, tasks[0]["id"])
    assert old["state"] == "failed" and old["error_code"] == "download_failed"
    assert queue.enqueue(config, binding, tasks[0]["job_id"])[0] == old
    retried = queue.retry(config, binding, old["id"])
    assert retried["attempt"] == 2 and retried["id"] != old["id"]
    assert queue.retry(config, binding, old["id"]) == retried
    assert queue.status(config, binding, old["id"]) == old


def test_crash_after_claim_requires_reconcile_before_any_fetch(config, binding, tasks):
    queue.claim(config, binding)
    with pytest.raises(InvalidInput, match="reconciliation"):
        asyncio.run(worker.dispatch(config, binding, fetcher=good))
    result = queue.reconcile(config, binding, tasks[0]["id"])
    assert result["state"] == "interrupted" and rows(config, "wx_recovered_media") == []
    assert queue.retry(config, binding, result["id"])["attempt"] == 2


def test_crash_after_byte_commit_settles_without_network(config, binding, tasks):
    queue.claim(config, binding)
    task = tasks[0]
    sha = recovery.view(config, binding, task["job_id"])["raw_sha256"]
    recovery.complete(config, binding, task["job_id"], sha, {task["item_key"]: b"saved"})
    assert queue.reconcile(config, binding, task["id"])["state"] == "succeeded"
    with pytest.raises(InvalidInput, match="not retryable"):
        queue.retry(config, binding, task["id"])


def test_cancel_stops_fetch_and_records_interruption(config, binding, tasks):
    async def scenario():
        entered = asyncio.Event()

        async def fetch(url):
            entered.set()
            await asyncio.Future()

        task = asyncio.create_task(worker.dispatch(config, binding, fetcher=fetch))
        await entered.wait()
        with pytest.raises(InvalidInput, match="active"):
            queue.reconcile(config, binding, tasks[0]["id"])
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert queue.status(config, binding, tasks[0]["id"])["state"] == "interrupted"
    with queue.worker_lock(config):
        pass
    assert rows(config, "wx_recovered_media") == []


def test_revoke_during_download_leaves_unsettled_not_success(config, binding, tasks):
    async def fetch(url):
        db = database.connect(config.state_root)
        auth.set_grant(
            db, binding.client_id, binding.vault_id, query=False, original=False, add=False
        )
        db.close()
        return b"bytes"

    result = asyncio.run(worker.dispatch(config, binding, fetcher=fetch))
    assert result["state"] == "blocked" and result["unsettled_attempt"] == tasks[0]["id"]
    assert rows(config, "wx_download_attempts")[0]["state"] == "running"
    assert rows(config, "wx_recovered_media") == []


def test_worker_conflict_and_invalid_limit_never_download(config, binding, tasks):
    with queue.worker_lock(config), pytest.raises(InvalidInput, match="active"):
        asyncio.run(worker.dispatch(config, binding, fetcher=good))
    for value in [0, True, 101]:
        with pytest.raises(InvalidInput):
            asyncio.run(worker.dispatch(config, binding, max_items=value, fetcher=good))
    assert all(row["state"] == "queued" for row in rows(config, "wx_download_attempts"))


def test_queue_does_not_create_knowledge_or_model_tasks(config, binding, tasks):
    asyncio.run(worker.dispatch(config, binding, max_items=2, fetcher=good))
    db = database.connect(config.state_root)
    for table in ["receipts", "session_refs", "execution_runs", "model_calls"]:
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    db.close()


def test_settlement_failure_after_bytes_is_reconciled(config, binding, tasks, monkeypatch):
    original = queue.finish

    def fail(*args, **kwargs):
        raise OSError("synthetic settlement failure")

    monkeypatch.setattr(queue, "finish", fail)
    with pytest.raises(OSError):
        asyncio.run(worker.dispatch(config, binding, fetcher=good))
    monkeypatch.setattr(queue, "finish", original)
    assert queue.status(config, binding, tasks[0]["id"])["state"] == "running"
    assert queue.reconcile(config, binding, tasks[0]["id"])["state"] == "succeeded"


def test_process_exit_after_claim_releases_lock_and_preserves_attempt(config, binding, tasks):
    import json
    import os
    import subprocess
    import sys
    from dataclasses import asdict
    from pathlib import Path

    script = """
import json, os, sys
from pathlib import Path
from fiber_bundle.config import load_config
from fiber_bundle.weixin_events import Binding
from fiber_bundle import weixin_media_queue as queue
root=Path(sys.argv[1])
config=load_config(root/'config.json', root)
binding=Binding(**json.loads(sys.argv[2]))
with queue.worker_lock(config):
    queue.claim(config,binding)
    os._exit(73)
"""
    env = dict(os.environ, PYTHONPATH=str(Path(queue.__file__).parent.parent))
    result = subprocess.run(
        [sys.executable, "-c", script, str(config.project_root), json.dumps(asdict(binding))],
        env=env,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 73
    assert queue.status(config, binding, tasks[0]["id"])["state"] == "running"
    assert queue.reconcile(config, binding, tasks[0]["id"])["state"] == "interrupted"


def test_cancelled_batch_can_reconcile_but_cannot_retry(config, binding, tasks):
    from datetime import UTC, datetime, timedelta

    from fiber_bundle import weixin_batches

    queue.claim(config, binding)
    weixin_batches.initialize(config, binding)
    batch = weixin_batches.advance(config, binding, now=datetime.now(UTC) + timedelta(seconds=5))[0]
    weixin_batches.choose(config, binding, batch["batch_id"], "cancel")
    assert queue.reconcile(config, binding, tasks[0]["id"])["state"] == "interrupted"
    with pytest.raises(InvalidInput, match="cancelled"):
        queue.retry(config, binding, tasks[0]["id"])

    async def forbidden(url):
        pytest.fail("cancelled batch cannot download")

    assert asyncio.run(worker.dispatch(config, binding, fetcher=forbidden))["state"] == "stopped"
