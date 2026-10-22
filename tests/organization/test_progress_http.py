"""Progress reads yield on contention and retain the response-delivery lease."""

import asyncio
import json
import threading
import time

from fiber_bundle import auth, database
from fiber_bundle.http_guard import Guard, Settings
from fiber_bundle.organization import overview


def test_metadata_transaction_contention_is_bounded_and_recovers(config, store, monkeypatch):
    auth.set_password(config, "synthetic-progress-password")
    with database.connect(config.state_root) as db:
        token = auth.login(db, "synthetic-progress-password", 3600)[0]
    entered = threading.Event()
    released = threading.Event()

    def owner():
        with database.connect(config.state_root) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            entered.set()
            released.wait(5)
            db.rollback()

    thread = threading.Thread(target=owner)
    thread.start()
    assert entered.wait(2)
    calls = []
    monkeypatch.setattr(overview, "progress", lambda *a: calls.append("GET") or {})

    async def app(scope, receive, send):
        calls.append(scope["method"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    async def run():
        guard = Guard(app, config, Settings())
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/admin/organization/progress",
            "headers": [
                (b"host", b"127.0.0.1:8765"),
                (b"cookie", ("fiber_session=" + token).encode()),
            ],
            "query_string": b"client_id=synthetic&vault_id=synthetic",
            "client": ("127.0.0.1", 1),
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        output = []

        async def send(value):
            output.append(value)

        start = time.monotonic()
        await guard(scope, receive, send)
        assert time.monotonic() - start < 1.5
        assert output[0]["status"] == 503
        assert json.loads(output[1]["body"])["error"] == "organization_progress_busy"
        assert not calls
        released.set()
        thread.join(2)
        output.clear()
        await guard(scope, receive, send)
        assert output[0]["status"] == 200
        assert calls == ["GET"]
        # Even another queued HTTP response cannot accumulate progress waiters.
        serial_entered, serial_release = asyncio.Event(), asyncio.Event()

        async def response_owner():
            async with guard.serial:
                serial_entered.set()
                await serial_release.wait()

        owned = asyncio.create_task(response_owner())
        await serial_entered.wait()
        try:
            output.clear()
            await guard(scope, receive, send)
            assert output[0]["status"] == 200
            assert calls == ["GET", "GET"]
        finally:
            serial_release.set()
            await owned

    try:
        asyncio.run(run())
    finally:
        released.set()
        thread.join(2)
