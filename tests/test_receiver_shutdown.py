"""Cancellation keeps thread ownership and shutdown has a process-level deadline."""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from fiber_bundle import feishu_sync
from fiber_bundle.channels import intake_background, intake_runtime
from fiber_bundle.channels.receiver_shutdown import deadline

pytest_plugins = ["test_channel_intake"]


@pytest.mark.parametrize("fails", [False, True])
def test_repeated_cancel_joins_thread_and_keeps_cancellation(fails):
    started, release, ended = threading.Event(), threading.Event(), threading.Event()

    def work():
        started.set()
        assert release.wait(3)
        ended.set()
        if fails:
            raise sqlite3.OperationalError("synthetic storage contention")

    async def check():
        task = asyncio.create_task(feishu_sync.run(work))
        while not started.is_set():
            await asyncio.sleep(0.001)
        try:
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0.01)
                assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError) as error:
            await asyncio.wait_for(task, 2)
        assert ended.is_set() and task.cancelled()
        if fails:
            assert isinstance(error.value.__cause__, sqlite3.OperationalError)

    asyncio.run(check())


def test_cancel_during_real_sqlite_busy_joins_intake_without_review_loop(
    config, entry, tmp_path, monkeypatch
):
    path = tmp_path / "busy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE receipts(state TEXT)")
        db.execute("INSERT INTO receipts VALUES ('stored')")
    blocker = sqlite3.connect(path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    started, ended = threading.Event(), threading.Event()
    calls = []

    def work():
        try:
            with sqlite3.connect(path, timeout=0.1) as db:
                db.set_trace_callback(
                    lambda sql: started.set() if sql == "BEGIN IMMEDIATE" else None
                )
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE receipts SET state='sending'")
        finally:
            ended.set()

    async def tick(*args):
        calls.append(True)
        await feishu_sync.run(work)
        return "ready"

    monkeypatch.setattr(intake_runtime, "tick", tick)

    async def check():
        task = asyncio.create_task(intake_background.serve(config, entry[0]["id"], object()))
        while not started.is_set():
            await asyncio.sleep(0.001)
        task.cancel()
        done, pending = await asyncio.wait({task}, timeout=2)
        if pending:
            task.cancel()  # Clean up the old implementation's stuck review loop on failure.
            await asyncio.gather(task, return_exceptions=True)
        assert done == {task} and not pending
        assert task.cancelled() and ended.is_set()

    try:
        asyncio.run(check())
    finally:
        blocker.rollback()
        blocker.close()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT state FROM receipts").fetchall() == [("stored",)]
    assert calls == [True]
    assert not (config.state_root / "configs/hermes-feishu-synthetic/intake-status.json").exists()


def test_normal_cleanup_disarms_deadline(tmp_path):
    with deadline(tmp_path, 0.1):
        pass
    # Run in a child too: a late watchdog must not terminate after normal cleanup.
    result = _child(tmp_path, "with deadline(root, 0.05):\n    pass\ntime.sleep(0.15)\n")
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "shutdown-status.json").exists()


def _child(root, body):
    source = Path(feishu_sync.__file__).parents[1]
    code = (
        "from pathlib import Path\nimport time,sys,fcntl,os,threading\n"
        "from fiber_bundle.channels.receiver_shutdown import deadline\n"
        "root=Path(sys.argv[1])\n" + body
    )
    return subprocess.run(
        [sys.executable, "-B", "-c", code, str(root)],
        env={**os.environ, "PYTHONPATH": str(source)},
        capture_output=True,
        text=True,
        timeout=5,
    )


def test_deadline_ends_all_threads_before_releasing_process_lease(tmp_path):
    result = _child(
        tmp_path,
        """
fd=os.open(root/'lease',os.O_CREAT|os.O_RDWR,0o600)
fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
def writer():
    time.sleep(1)
    (root/'late-write').write_text('unsafe detached writer')
threading.Thread(target=writer).start()
with deadline(root, 0.05):
    time.sleep(10)
""",
    )
    assert result.returncode == 1, result.stderr
    status = json.loads((tmp_path / "shutdown-status.json").read_text())
    assert status["error"] == "channel_shutdown_timeout"
    assert not (tmp_path / "late-write").exists()
    import fcntl

    with (tmp_path / "lease").open("rb") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
