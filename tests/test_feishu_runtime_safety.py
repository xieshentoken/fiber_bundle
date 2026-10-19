"""Real journal reservations, bounded contention, and process-lease health evidence."""

import asyncio
import fcntl
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_feishu_ingress import feishu as feishu
from test_feishu_ingress import freeze, payload
from test_weixin_inbox import binding as binding

from fiber_bundle import (
    database,
    feishu_budgets,
    feishu_gateway,
    feishu_gateway_loop,
    feishu_receiver,
    feishu_runtime,
    feishu_sync,
    weixin_batches,
    weixin_inbox,
    weixin_store_dispatch,
)
from fiber_bundle.config import InvalidInput


def test_wait_is_bounded_loop_stays_responsive_and_no_writer_survives(config, store):
    fd = os.open(config.state_root / "storage.lock", os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    wrote = []

    def work():
        with database.locked(config):
            wrote.append(True)

    async def check():
        task = asyncio.create_task(feishu_sync.run(work))
        ticks = 0
        while not task.done():
            await asyncio.sleep(0.025)
            ticks += 1
        with pytest.raises(TimeoutError):
            await task
        assert ticks > 10

    try:
        started = time.monotonic()
        asyncio.run(check())
        assert time.monotonic() - started < 2
    finally:
        os.close(fd)
    assert wrote == []
    with database.locked(config):
        with pytest.raises(RuntimeError, match="nested storage"):
            with database.locked(config):
                pytest.fail("nested lock allowed")


def test_cancel_joins_owned_worker_before_return():
    started, release = threading.Event(), threading.Event()
    ended = []

    def work():
        started.set()
        release.wait(2)
        ended.append(True)

    async def check():
        task = asyncio.create_task(feishu_sync.run(work))
        while not started.is_set():
            await asyncio.sleep(0.005)
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert ended == [True]

    asyncio.run(check())


def test_budget_survives_restart_unknown_outcome_and_new_grant(config, feishu):
    identity = str(uuid4())
    feishu_budgets.authorize(config, feishu, identity, 1, 1)
    with weixin_inbox.connection(config, feishu) as db, database.transaction(db):
        assert feishu_budgets.reserve_locked(db, feishu, identity, "download", "synthetic:0")
    feishu_budgets.authorize(config, feishu, identity, 1, 1)
    assert feishu_budgets.remaining(config, feishu, identity)["download"] == 0
    with weixin_inbox.connection(config, feishu) as db, database.transaction(db):
        assert not feishu_budgets.reserve_locked(db, feishu, identity, "download", "new:0")
        assert not feishu_budgets.reserve_locked(db, feishu, identity, "download", "synthetic:0")
    with pytest.raises(InvalidInput):
        feishu_budgets.authorize(config, feishu, identity, 2, 1)
    renewed = str(uuid4())
    feishu_budgets.authorize(config, feishu, renewed, 1, 0)
    assert feishu_budgets.remaining(config, feishu, renewed)["download"] == 1
    assert feishu_budgets.remaining(config, feishu, None)["download"] == 0
    with pytest.raises(InvalidInput):
        feishu_budgets.authorize(config, feishu, None, 1, 0)


def test_store_reservation_is_atomic_and_consumed_on_failure(config, feishu, monkeypatch):
    identity = str(uuid4())
    feishu_budgets.authorize(config, feishu, identity, 0, 1)
    feishu_receiver.capture(config, feishu, payload())
    batch = freeze(config, feishu)
    weixin_batches.choose(config, feishu, batch, "save_one")

    def fail(*args):
        assert feishu_budgets.remaining(config, feishu, identity)["store"] == 0
        raise OSError("synthetic failed write")

    monkeypatch.setattr(weixin_store_dispatch.handoff, "transfer", fail)
    with pytest.raises(OSError):
        weixin_store_dispatch.drain(
            config,
            feishu,
            maximum=1,
            reserve=lambda db, target: feishu_budgets.reserve_locked(
                db, feishu, identity, "store", target
            ),
        )
    feishu_budgets.authorize(config, feishu, identity, 0, 1)
    assert feishu_budgets.remaining(config, feishu, identity)["store"] == 0


def test_concurrent_reservations_do_not_overdraw_and_idle_drain_consumes_nothing(config, feishu):
    identity = str(uuid4())
    feishu_budgets.authorize(config, feishu, identity, 1, 1)
    assert (
        weixin_store_dispatch.drain(
            config,
            feishu,
            maximum=1,
            reserve=lambda db, target: feishu_budgets.reserve_locked(
                db, feishu, identity, "store", target
            ),
        )
        == 0
    )
    assert feishu_budgets.remaining(config, feishu, identity)["store"] == 1

    def consume(target):
        with weixin_inbox.connection(config, feishu) as db, database.transaction(db):
            return feishu_budgets.reserve_locked(db, feishu, identity, "download", str(target))

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(consume, range(8))) == 1
    assert feishu_budgets.remaining(config, feishu, identity)["download"] == 0


def test_gateway_heartbeat_and_stop_during_lock_contention(config, feishu):
    home = config.state_root / "configs/hermes-feishu-synthetic"
    home.mkdir(parents=True)
    settings = feishu_gateway.Settings(
        "cli_synthetic",
        feishu.owner_id,
        feishu.client_id,
        feishu.vault_id,
        "configs/hermes-feishu-synthetic",
        "/synthetic/secret.txt",
        enabled=True,
    )
    descriptor = None

    class Runner:
        adapters = {"feishu": SimpleNamespace(_fiber_error=None, is_connected=True)}

        async def start(self):
            nonlocal descriptor
            descriptor = os.open(config.state_root / "storage.lock", os.O_RDWR)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            return True

        async def stop(self):
            os.close(descriptor)

    async def check():
        stop = asyncio.Event()
        task = asyncio.create_task(
            feishu_gateway_loop.run(config, settings, home, Runner(), "feishu", stop)
        )
        for _ in range(10):
            await asyncio.sleep(0.03)
        assert not task.done()
        data = json.loads((home / "fiber-status.json").read_bytes())
        assert data["instance_id"] and time.time() - data["heartbeat_at"] < 1.2
        stop.set()
        assert await asyncio.wait_for(task, timeout=3) == {"state": "stopped"}
        assert json.loads((home / "fiber-status.json").read_bytes())["state"] == "stopped"
        assert not feishu_runtime.inspect(home)["available"]

    asyncio.run(check())


def test_status_requires_fresh_matching_live_lease_and_duplicate_start_is_denied(tmp_path):
    with feishu_runtime.Instance(tmp_path) as instance:
        instance.status("connected", {"download": 0, "store": 0})
        assert feishu_runtime.inspect(tmp_path)["available"]
        with pytest.raises(InvalidInput):
            with feishu_runtime.Instance(tmp_path):
                pytest.fail("duplicate instance")
        data = json.loads((tmp_path / "fiber-status.json").read_bytes())
        data["instance_id"] = str(uuid4())
        feishu_runtime.write(tmp_path / "fiber-status.json", data)
        assert not feishu_runtime.inspect(tmp_path)["available"]
        instance.status("connected", {}, processing="storage_busy")
        assert not feishu_runtime.inspect(tmp_path)["available"]
        instance.status("connected", {})
        assert not feishu_runtime.inspect(tmp_path, maximum_age=0)["available"]
    assert not feishu_runtime.inspect(tmp_path)["available"]


def test_forced_exit_old_connected_file_is_unavailable(tmp_path):
    script = (
        "import sys,time;from pathlib import Path;from fiber_bundle.feishu_runtime import Instance;"
        "p=Instance(Path(sys.argv[1]));p.__enter__();p.status('connected',{});"
        "print('ready',flush=True);time.sleep(30)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        env={**os.environ, "PYTHONPATH": "src"},
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == "ready"
        assert feishu_runtime.inspect(tmp_path)["available"]
        process.kill()
        process.wait(timeout=5)
        assert not feishu_runtime.inspect(tmp_path)["available"]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
