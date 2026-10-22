"""Contended reads are bounded; public health does not queue behind content delivery."""

import asyncio
import json
import threading
import time

from fiber_bundle import database
from fiber_bundle.http_guard import Guard, Settings
from fiber_bundle.http_observation import Observations


def scope(path="/admin/components", method="GET"):
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(b"host", b"127.0.0.1:8765"), (b"origin", b"http://127.0.0.1:8765")],
        "client": ("127.0.0.1", 1),
    }


async def call(guard, request):
    output = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        output.append(message)

    await guard(request, receive, send)
    return output


def test_storage_busy_is_bounded_no_handler_runs_then_recovers(config, store):
    entered, release = threading.Event(), threading.Event()

    def owner():
        with database.locked(config):
            entered.set()
            release.wait(8)

    thread = threading.Thread(target=owner)
    thread.start()
    assert entered.wait(2)
    executed = []

    async def app(request, receive, send):
        executed.append(request["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    async def run():
        observations = Observations()
        guard = Guard(app, config, Settings(), observations)
        for path in ["/health", "/static/atlas/main.js", "/"]:
            started = time.monotonic()
            assert (await call(guard, scope(path)))[0]["status"] == 200
            assert time.monotonic() - started < 0.5
        for method in ["GET", "POST"]:
            started = time.monotonic()
            response = await call(guard, scope(method=method))
            assert time.monotonic() - started < 2.5
            assert response[0]["status"] == 503
            assert json.loads(response[1]["body"])["error"] == "storage_busy"
            assert (b"retry-after", b"1") in response[0]["headers"]
        assert executed == ["/health", "/static/atlas/main.js", "/"]
        assert all(e["stage"] == "storage" for e in observations.entries)
        assert all(e["route"] == "/admin/components" for e in observations.entries)
        release.set()
        await asyncio.to_thread(thread.join, 2)
        assert (await call(guard, scope()))[0]["status"] == 200

    try:
        asyncio.run(run())
    finally:
        release.set()
        thread.join(2)


def test_queue_busy_yields_and_health_remains_reachable(config, store):
    entered, release = asyncio.Event(), asyncio.Event()

    async def app(request, receive, send):
        if request["path"] == "/admin/slow":
            entered.set()
            await release.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    async def run():
        observed = Observations()
        guard = Guard(app, config, Settings(), observed)
        slow = asyncio.create_task(call(guard, scope("/admin/slow")))
        await entered.wait()
        assert (await call(guard, scope("/health")))[0]["status"] == 200
        busy = await call(guard, scope())
        assert busy[0]["status"] == 503
        assert observed.entries[-1]["stage"] == "queue"
        release.set()
        assert (await slow)[0]["status"] == 200
        assert (await call(guard, scope()))[0]["status"] == 200

    asyncio.run(run())


def test_observations_bound_and_redact_unknown_paths():
    observations = Observations()
    for _ in range(70):
        observations.record(scope("/private/do-not-record-this"), 503, 2, {"stage": "storage"})
    assert len(observations.entries) == 64
    assert all(e["route"] == "/other" for e in observations.entries)
    assert "do-not-record-this" not in json.dumps(list(observations.entries))


def test_http_diagnostics_requires_admin(http):
    assert http.get("/admin/http-diagnostics").json()["scope"] == "current_process"
    http.cookies.clear()
    assert http.get("/admin/http-diagnostics").status_code == 401
