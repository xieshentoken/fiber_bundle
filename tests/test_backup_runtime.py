"""A blocked backup ledger must not stop the manager or execute unrecovered work."""

import threading

import pytest

from fiber_bundle.backup_automation import runner, runtime, scheduler
from fiber_bundle.config import InvalidInput


def test_oversize_ledger_does_not_stop_backend(config, store):
    from fastapi.testclient import TestClient

    from fiber_bundle.api import create_app

    path = config.project_root / ".backup-automation" / "state.json"
    path.parent.mkdir()
    with path.open("wb") as handle:
        handle.truncate(64 * 1024 * 1024 + 1)
    with TestClient(
        create_app(config), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 12345)
    ) as client:
        assert client.get("/health").status_code == 200
        with pytest.raises(InvalidInput, match="backup_ledger_limit"):
            runtime.store.read(config)
        assert path.stat().st_size == 64 * 1024 * 1024 + 1


def test_recovery_must_succeed_before_backup_work(config, monkeypatch):
    recovered = threading.Event()
    calls = []
    instance = runtime.Runtime(config)

    def recover(_):
        calls.append("recover")
        if calls.count("recover") == 1:
            raise InvalidInput("backup_ledger_limit")
        recovered.set()

    def execute(_):
        assert recovered.is_set()
        calls.append("execute")

    def tick(_, **kwargs):
        calls.append("tick")
        instance.stop.set()

    monkeypatch.setattr(runner, "recover", recover)
    monkeypatch.setattr(runner, "execute", execute)
    monkeypatch.setattr(scheduler, "tick", tick)
    instance.start()
    assert instance.thread is not None
    instance.thread.join(timeout=5)
    instance.close()
    assert calls == ["recover", "recover", "execute", "tick"]
