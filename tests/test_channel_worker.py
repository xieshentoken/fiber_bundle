"""Run the production worker loop and lease guards; substitute only the external SDK."""

import asyncio
import json
import sys
import time
from uuid import uuid4

import pytest
from test_message_channels import bind
from test_message_channels import channel as channel

from fiber_bundle import database, feishu_runtime
from fiber_bundle.channels import control, store, worker
from fiber_bundle.config import InvalidInput


def launch_file(config, identity):
    value = {
        "storage": config.storage_configuration(),
        "channel_id": identity,
        "revision": 1,
        "action": "run",
        "nonce": str(uuid4()),
    }
    path = store.home(config, identity) / "launch.json"
    path.write_text(json.dumps(value))
    return value, path


@pytest.mark.parametrize("platform", ["feishu", "weixin"])
def test_query_only_worker_stays_connected_without_download_and_exits_on_pause(
    config, channel, monkeypatch, platform
):
    value, _ = channel
    bind(config, value)
    with store.opened(config) as side:
        record = store.get(side, value["id"])
        record["value"]["platform"] = platform
        side.execute("UPDATE channels SET value=?", (json.dumps(record["value"]),))
    launch, path = launch_file(config, value["id"])

    class Adapter:
        is_connected = True
        _fiber_error = None

        async def connect(self):
            return True

        async def disconnect(self):
            self.is_connected = False

    adapter = Adapter()
    module = worker.feishu if platform == "feishu" else worker.weixin
    monkeypatch.setattr(module, "adapter", lambda *args: adapter)
    monkeypatch.setattr(worker.feishu_gateway_cli, "isolate", lambda *args: None)
    monkeypatch.setattr(worker.feishu_gateway_cli, "bootstrap", lambda *args: None)
    monkeypatch.setattr(
        worker.feishu_receiver,
        "download_one",
        lambda *args, **kwargs: pytest.fail("query-only entry attempted a download"),
    )

    async def exercise():
        task = asyncio.create_task(worker.run(config, launch))
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if task.done():
                    await task
                    pytest.fail("worker exited before permission withdrawal")
                status = control.status(config, value["id"])
                if status.get("state") == "query_ready":
                    break
                await asyncio.sleep(0.05)
            assert status.get("state") == "query_ready"
            # Let another complete iteration run; the previous indentation bug exits here.
            await asyncio.sleep(1.1)
            assert not task.done()
            assert control.status(config, value["id"])["available"]
            main = database.connect(config.state_root)
            try:
                control.pause(config, main, value["id"])
            finally:
                main.close()
            with pytest.raises(InvalidInput, match="withdrawn"):
                await asyncio.wait_for(task, 3)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
    assert not adapter.is_connected
    assert feishu_runtime.inspect(path.parent)["process_state"] == "stopped"


def test_delayed_worker_with_superseded_nonce_never_bootstraps(config, channel, monkeypatch):
    value, _ = channel
    bind(config, value)
    launch, path = launch_file(config, value["id"])
    path.write_text(json.dumps({**launch, "nonce": str(uuid4())}))
    monkeypatch.setattr(
        worker.feishu_gateway_cli,
        "bootstrap",
        lambda *args: pytest.fail("superseded launch imported SDK"),
    )
    with pytest.raises(InvalidInput, match="launch_withdrawn"):
        asyncio.run(worker.run(config, launch))
    with store.readonly(config) as side:
        assert store.get(side, value["id"])["state"] == "running"


def test_failed_old_launch_cannot_mark_a_new_launch_as_error(config, channel, monkeypatch):
    value, _ = channel
    bind(config, value)
    launch, path = launch_file(config, value["id"])

    def bootstrap(*args):
        path.write_text(json.dumps({**launch, "nonce": str(uuid4())}))
        raise InvalidInput("channel_launch_withdrawn")

    monkeypatch.setattr(worker.feishu_gateway_cli, "isolate", lambda *args: None)
    monkeypatch.setattr(worker.feishu_gateway_cli, "bootstrap", bootstrap)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "worker",
            "--project-root",
            str(config.project_root),
            "--configuration",
            str(path),
            "--nonce",
            launch["nonce"],
        ],
    )
    assert worker.main() == 1
    with store.readonly(config) as side:
        assert store.get(side, value["id"])["state"] == "running"
    assert not (path.parent / "check-result.json").exists()


def test_poll_failure_retains_safe_cause_in_worker_diagnostic(config, channel, monkeypatch):
    value, _ = channel
    bind(config, value)
    launch, path = launch_file(config, value["id"])

    class Adapter:
        is_connected = True
        _fiber_error = "channel_weixin_poll_failed"
        _fiber_exception = InvalidInput("channel_weixin_poll_exhausted")

        async def connect(self):
            return True

        async def disconnect(self):
            self.is_connected = False

    monkeypatch.setattr(worker.feishu, "adapter", lambda *args: Adapter())
    monkeypatch.setattr(worker.feishu_gateway_cli, "isolate", lambda *args: None)
    monkeypatch.setattr(worker.feishu_gateway_cli, "bootstrap", lambda *args: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "worker",
            "--project-root",
            str(config.project_root),
            "--configuration",
            str(path),
            "--nonce",
            launch["nonce"],
        ],
    )
    assert worker.main() == 1
    report = json.loads((path.parent / "check-result.json").read_text())
    assert report["error"] == "channel_weixin_poll_exhausted"
    assert report["diagnostic"]["locations"]
