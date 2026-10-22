"""Real HTTP progress/control while the writer is occupied; no production fixtures."""

import threading
import time

import pytest
from test_execution_leases import client, confirmed
from test_jobs import ready as ready

from fiber_bundle import auth, database
from fiber_bundle.organization import execution, execution_control, store


def test_progress_and_pause_bypass_long_writer_and_http_queue(config, ready):
    job = confirmed(config, ready, two=True)
    c = client(config)
    entered, release = threading.Event(), threading.Event()

    def writer():
        with database.locked(config):
            entered.set()
            assert release.wait(10)

    thread = threading.Thread(target=writer)
    thread.start()
    assert entered.wait(2)
    scope = {k: job[k] for k in ("client_id", "vault_id")}
    try:
        started = time.monotonic()
        for path in ("/admin/backend", "/admin/vaults", "/admin/clients", "/admin/session"):
            assert c.get(path).status_code == 200, path
        for _ in range(3):
            r = c.get("/admin/organization/progress", params=scope)
            assert r.status_code == 200, r.text
        r = c.post(f"/admin/organization/maintenance/{job['id']}/pause", json=scope)
        assert r.status_code == 200, r.text
        assert r.json()["pause_requested"] or r.json()["state"] == "paused"
        assert time.monotonic() - started < 2
        assert store.get(config, "jobs", job["id"])["completed"] == 0
        # Origin and CSRF remain mandatory; an arbitrary scope cannot pause this job.
        csrf = c.headers.pop("X-CSRF-Token")
        assert (
            c.post(f"/admin/organization/maintenance/{job['id']}/pause", json=scope).status_code
            == 403
        )
        c.headers["X-CSRF-Token"] = csrf
        assert c.post(
            f"/admin/organization/maintenance/{job['id']}/pause",
            json={**scope, "client_id": "other"},
        ).status_code in {403, 404}
    finally:
        release.set()
        thread.join(2)
    execution.run(config, job["id"])
    assert store.get(config, "jobs", job["id"])["state"] == "paused"
    assert not execution_control.requested(config, store.get(config, "jobs", job["id"]))
    c.close()


def test_revoked_scope_and_unauthenticated_control_cannot_create_intent(config, ready):
    job = confirmed(config, ready)
    c = client(config)
    scope = {k: job[k] for k in ("client_id", "vault_id")}
    with database.locked(config), database.connect(config.state_root) as db:
        auth.set_grant(
            db, job["client_id"], job["vault_id"], query=False, original=False, add=False
        )
    assert c.get("/admin/organization/progress", params=scope).status_code == 404
    assert (
        c.post(f"/admin/organization/maintenance/{job['id']}/pause", json=scope).status_code == 404
    )
    assert not execution_control.requested(config, job)
    c.cookies.clear()
    assert c.get("/admin/organization/progress", params=scope).status_code == 401
    assert (
        c.post(f"/admin/organization/maintenance/{job['id']}/pause", json=scope).status_code == 401
    )
    assert not execution_control.requested(config, job)
    c.close()


@pytest.mark.parametrize("mode", ["wal", "delete"])
def test_authorization_reservation_survives_delivery(config, ready, mode):
    import asyncio
    from unittest.mock import patch

    from fiber_bundle.http_guard import Guard, Settings
    from fiber_bundle.organization import overview

    c = client(config)
    token = c.cookies.get(auth.COOKIE)
    with database.connect(config.state_root) as db:
        assert db.execute("PRAGMA journal_mode=" + mode).fetchone()[0] == mode
    writing, committed = threading.Event(), threading.Event()

    def revoke():
        with database.connect(config.state_root) as db:
            writing.set()
            db.execute("DELETE FROM admin_sessions")
            committed.set()

    async def run():
        async def app(*args):
            raise AssertionError("control must use its independent reservation")

        guard = Guard(app, config, Settings())
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/admin/organization/progress",
            "headers": [
                (b"host", b"127.0.0.1:8765"),
                (b"cookie", (auth.COOKIE + "=" + token).encode()),
            ],
            "query_string": b"client_id=synthetic&vault_id=synthetic",
            "client": ("127.0.0.1", 1),
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        thread = None

        async def send(message):
            nonlocal thread
            if message["type"] == "http.response.start":
                assert message["status"] == 200
                thread = threading.Thread(target=revoke)
                thread.start()
                await asyncio.to_thread(writing.wait, 2)
                await asyncio.sleep(0.05)
                assert not committed.is_set(), "revocation committed during authorized delivery"

        with patch.object(overview, "progress", lambda *a: {}):
            await guard(scope, receive, send)
        await asyncio.to_thread(thread.join, 2)
        assert committed.is_set()

    asyncio.run(run())
    c.close()


def test_metadata_writer_reports_pause_not_submitted_and_keeps_job(config, ready):
    job = confirmed(config, ready)
    c = client(config)
    entered, release = threading.Event(), threading.Event()

    def writer():
        with database.connect(config.state_root) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            entered.set()
            release.wait(5)
            db.rollback()

    thread = threading.Thread(target=writer)
    thread.start()
    assert entered.wait(2)
    try:
        result = c.post(
            f"/admin/organization/maintenance/{job['id']}/pause",
            json={k: job[k] for k in ("client_id", "vault_id")},
        )
        assert result.status_code == 503
        assert result.json()["error"] == "organization_control_busy"
        assert not execution_control.requested(config, job)
        assert store.get(config, "jobs", job["id"]) == job
    finally:
        release.set()
        thread.join(2)
        c.close()


def test_pause_racing_confirmation_is_retained_before_any_note_write(config, ready, monkeypatch):
    from test_maintenance import preview
    from fiber_bundle.organization import maintenance_moc_recovery

    c = client(config)
    cid, vid, _, moc, note = ready
    before = (moc.read_bytes(), note.read_bytes())
    with database.locked(config), database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
    entered, release = threading.Event(), threading.Event()
    original = maintenance_moc_recovery.preflight

    def slow(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(maintenance_moc_recovery, "preflight", slow)
    result = []
    scope = {"client_id": cid, "vault_id": vid}
    thread = threading.Thread(
        target=lambda: result.append(
            c.post(
                f"/admin/organization/maintenance/{job['id']}/confirm",
                json={**scope, "plan_sha256": job["plan_sha256"]},
            )
        )
    )
    thread.start()
    try:
        assert entered.wait(5)
        response = c.post(f"/admin/organization/maintenance/{job['id']}/pause", json=scope)
        assert response.status_code == 200 and response.json()["pause_requested"], response.text
    finally:
        release.set()
        thread.join(10)
    assert result[0].status_code == 200
    execution.run(config, job["id"])
    current = store.get(config, "jobs", job["id"])
    assert current["state"] == "paused" and current["completed"] == 0
    assert before == (moc.read_bytes(), note.read_bytes())
    c.close()
