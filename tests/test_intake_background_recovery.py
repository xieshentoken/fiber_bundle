"""Storage contention recovers without replaying unknown network or model operations."""

import asyncio
import fcntl
import json
import os
from contextlib import suppress

import pytest

from fiber_bundle import database, feishu_runtime, feishu_sync
from fiber_bundle.channels import intake_background, intake_runtime

pytest_plugins = ["test_channel_intake"]


def test_real_storage_lease_timeout_does_not_permanently_disable_intake(config, entry, monkeypatch):
    fd = os.open(config.state_root / "storage.lock", os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    observed, work = [], []
    original_write, original_sleep = feishu_runtime.write, asyncio.sleep

    def write(path, value):
        observed.append(value["state"])
        original_write(path, value)

    async def pause(_):
        if "waiting_storage" in observed:
            fcntl.flock(fd, fcntl.LOCK_UN)
        if "ready" in observed:
            raise asyncio.CancelledError()
        await original_sleep(0)

    def guarded():
        with database.locked(config):
            work.append(True)

    async def tick(*args):
        await feishu_sync.run(guarded)
        return "ready"

    monkeypatch.setattr(feishu_runtime, "write", write)
    monkeypatch.setattr(intake_runtime, "tick", tick)
    monkeypatch.setattr(asyncio, "sleep", pause)

    async def check():
        try:
            await intake_background.serve(config, entry[0]["id"], object())
        except asyncio.CancelledError:
            pass

    try:
        asyncio.run(check())
    finally:
        os.close(fd)
    assert observed == ["waiting_storage", "ready"]
    assert work == [True]


def test_unknown_external_timeout_stays_visible_without_automatic_retry(config, entry, monkeypatch):
    called, written = [], asyncio.Event()
    original = feishu_runtime.write

    async def tick(*args):
        called.append(True)
        raise TimeoutError("private provider response")

    def write(path, value):
        original(path, value)
        written.set()

    monkeypatch.setattr(intake_runtime, "tick", tick)
    monkeypatch.setattr(feishu_runtime, "write", write)

    async def check():
        task = asyncio.create_task(intake_background.serve(config, entry[0]["id"], object()))
        await written.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(check())
    path = config.state_root / "configs/hermes-feishu-synthetic/intake-status.json"
    result = json.loads(path.read_text())
    assert result["state"] == "needs_review"
    assert result["error"] == "channel_connection_timeout"
    assert "private provider" not in path.read_text()
    assert called == [True]


def test_long_running_tick_has_owned_progress_heartbeat(config, entry, monkeypatch):
    called, processing = [], asyncio.Event()
    original = feishu_runtime.write

    async def tick(*args):
        called.append(True)
        await asyncio.Future()

    def write(path, value):
        original(path, value)
        if value["state"] == "processing":
            processing.set()

    monkeypatch.setattr(intake_runtime, "tick", tick)
    monkeypatch.setattr(feishu_runtime, "write", write)

    async def check():
        task = asyncio.create_task(intake_background.serve(config, entry[0]["id"], object()))
        await asyncio.wait_for(processing.wait(), timeout=5)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(check())
    from fiber_bundle.channels.batch_intake import worker_status

    result = worker_status(config, entry[0])
    assert result["state"] == "processing" and result["fresh"]
    assert called == [True]


def test_cancellation_during_heartbeat_join_does_not_restart_intake(config, entry, monkeypatch):
    started, joining = asyncio.Event(), asyncio.Event()
    calls = []

    async def heartbeat(path):
        try:
            started.set()
            await asyncio.Future()
        finally:
            joining.set()
            await asyncio.Future()

    async def tick(*args):
        calls.append(True)
        await started.wait()
        return "ready"

    monkeypatch.setattr(intake_background, "heartbeat", heartbeat)
    monkeypatch.setattr(intake_runtime, "tick", tick)

    async def check():
        task = asyncio.create_task(intake_background.serve(config, entry[0]["id"], object()))
        await asyncio.wait_for(joining.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)

    asyncio.run(check())
    assert calls == [True]
