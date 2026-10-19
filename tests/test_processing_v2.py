"""Quota-free intake uses the real isolated journal/Store, preserving legacy contracts."""

import asyncio
import json

from test_channel_intake import count, enable, receive, records

from fiber_bundle.channels import (
    intake_background,
    intake_contract,
    intake_processing,
    legacy,
    store,
)
from fiber_bundle.channels import intake_repository as repository

pytest_plugins = ["test_channel_intake"]


def test_v2_archives_multiple_batches_with_all_business_limits_zero(config, entry):
    enable(config, entry, policy_version=2, store_limit=0, download_limit=0)
    for identity in ("first", "second", "third"):
        receive(config, entry, identity=identity)
        assert intake_processing.advance(config, entry[0]["id"]) == 1
    assert all(row["state"] == "stored" for row in records(config, entry))
    assert count(config, "receipts") == 3
    assert count(config, "model_calls") == count(config, "execution_runs") == 0
    with store.readonly(config) as db:
        shown = repository.present(db, entry[0])
        assert shown["remaining"]["store"] is None
        assert shown["remaining"]["download"] is None
        assert shown["remaining"]["outbound"] is None


def test_v2_never_replays_history_or_reenables_a_paused_policy(config, entry):
    receive(config, entry, identity="old")
    enable(config, entry, policy_version=2, enabled=False, store_limit=0, download_limit=0)
    assert intake_processing.advance(config, entry[0]["id"]) == 0
    enable(config, entry, policy_version=2, store_limit=0, download_limit=0)
    assert intake_processing.advance(config, entry[0]["id"]) == 0
    assert count(config, "receipts") == 0


def test_v1_is_the_explicit_legacy_default():
    assert intake_contract.Settings().policy_version == 1
    assert not repository.unlimited({}, "download")
    assert not repository.unlimited({"policy_version": 1}, "outbound")
    assert repository.unlimited({"policy_version": 2}, "outbound")


def test_slow_or_failed_intake_does_not_stop_other_tasks_or_replay(config, entry, monkeypatch):
    async def exercise():
        started = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def slow(*args):
            calls.append(1)
            started.set()
            await release.wait()
            raise TimeoutError("private synthetic message must not reach status")

        monkeypatch.setattr(intake_background.intake_runtime, "tick", slow)
        task = asyncio.create_task(intake_background.serve(config, entry[0]["id"], object()))
        try:
            await asyncio.wait_for(started.wait(), 1)
            await asyncio.sleep(0)
            assert not task.done()
            release.set()
            await asyncio.sleep(0.05)
            status = json.loads((legacy.home(config, entry[0]) / "intake-status.json").read_text())
            assert status["state"] == "needs_review"
            assert "private synthetic" not in json.dumps(status)
            assert not task.done() and len(calls) == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
