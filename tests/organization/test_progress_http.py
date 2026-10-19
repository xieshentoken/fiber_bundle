"""Progress reads yield on contention and retain the response-delivery lease."""

import asyncio
import json
import threading
import time

from fiber_bundle import database
from fiber_bundle.http_guard import Guard, Settings


def test_busy_progress_is_bounded_then_recovers_without_running_mutations(config, store):
    entered = threading.Event()
    released = threading.Event()

    def owner():
        with database.locked(config):
            entered.set()
            released.wait(5)

    thread = threading.Thread(target=owner)
    thread.start()
    assert entered.wait(2)
    calls = []

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
            "headers": [(b"host", b"127.0.0.1:8765")],
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
            assert output[0]["status"] == 503
            assert calls == ["GET"]
        finally:
            serial_release.set()
            await owned

    try:
        asyncio.run(run())
    finally:
        released.set()
        thread.join(2)
