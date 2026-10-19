import threading
from types import SimpleNamespace

import pytest
from test_jobs import ready as ready

from fiber_bundle import database
from fiber_bundle.organization import (
    maintenance,
    maintenance_analysis,
    model_review,
    semantic,
    store,
)


def pending(config, ready, monkeypatch):
    cid, vid, _, _, note = ready
    note.write_text("# 资料\n固体内部存在温度梯度。\n冷却液流动带走热量并保持温度稳定。\n")
    monkeypatch.setattr(model_review, "route", lambda *a: SimpleNamespace(digest="fixed"))
    monkeypatch.setattr(semantic, "recall", lambda *a: {"state": "disabled"})
    with database.connect(config.state_root) as db:
        job = maintenance.submit(config, db, cid, vid, "additive")
    assert not job["items"][0]["evaluated"]
    return job


def test_full_read_releases_writer_and_cancel_prevents_analysis(config, ready, monkeypatch):
    job = pending(config, ready, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original = maintenance.validate

    def scan(*args, **kwargs):
        if kwargs.get("sources", True):
            entered.set()
            assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(maintenance, "validate", scan)
    monkeypatch.setattr(
        model_review, "resolve", lambda *a: pytest.fail("cancelled task called model")
    )
    errors = []

    def run():
        try:
            maintenance_analysis.prepare(config, job["id"])
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    assert entered.wait(3)
    try:
        with database.lock_wait(0.2), database.locked(config):
            current = store.get(config, "jobs", job["id"])
            current["state"] = "cancelled"
            store.put(config, "jobs", job["id"], job["vault_id"], current)
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and not errors
    current = store.get(config, "jobs", job["id"])
    assert current["state"] == "cancelled" and not current["items"][0]["evaluated"]


@pytest.mark.parametrize(
    "target,code", [(3, "maintenance_moc_changed"), (4, "maintenance_note_changed")]
)
def test_source_change_during_model_prevents_result_commit(
    config, ready, monkeypatch, target, code
):
    job = pending(config, ready, monkeypatch)

    def changed(config, job, entity, mocs, item, check):
        ready[target].write_text(ready[target].read_text() + "\n用户新增内容\n")
        check()
        pytest.fail("source change accepted")

    monkeypatch.setattr(model_review, "resolve", changed)
    maintenance_analysis.prepare(config, job["id"])
    current = store.get(config, "jobs", job["id"])
    assert current["state"] == "blocked" and current["error"] == code
    assert not current["items"][0]["evaluated"] and not current["confirmed"]


def test_readonly_metadata_connection_cannot_write(config, ready):
    import sqlite3

    with database.connect(config.state_root, readonly=True) as db:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("CREATE TABLE forbidden(value TEXT)")
    with pytest.raises(ValueError):
        database.connect(config.state_root, readonly=True, create=True)
