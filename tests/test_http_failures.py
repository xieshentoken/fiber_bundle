"""Failure categories stay truthful before, during and after protected display reads."""

import asyncio
import json
import sqlite3

import pytest
from starlette.routing import Route
from test_display_reads import call, login, request

from fiber_bundle import display_http
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.http_guard import Guard, Settings
from fiber_bundle.http_observation import Observations


@pytest.mark.parametrize(
    "failure,code,status",
    [
        (PermissionError("secret directory"), "storage_unavailable", 503),
        (sqlite3.DatabaseError("secret database"), "storage_unavailable", 503),
        (IntegrityError("unknown private check"), "storage_unavailable", 503),
        (InvalidInput("secret invalid path"), "invalid_input", 422),
        (IntegrityError("display_source_changed"), "display_changed", 503),
        (IntegrityError("display_observation_limit"), "display_limit_exceeded", 503),
        (IntegrityError("display_response_limit"), "display_limit_exceeded", 503),
    ],
)
@pytest.mark.parametrize("phase", ["prepare", "compute", "deliver"])
def test_display_errors_are_not_busy_and_do_not_leak(
    config, store, monkeypatch, failure, code, status, phase
):
    token = login(config)
    original, calls = display_http.stamp, []

    def stamp(*args):
        calls.append(True)
        if phase == "prepare" or (phase == "deliver" and len(calls) == 2):
            raise failure
        return original(*args)

    monkeypatch.setattr(display_http, "stamp", stamp)

    async def app(scope, receive, send):
        if phase == "compute":
            raise failure
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"private-result"})

    result = asyncio.run(call(Guard(app, config, Settings()), request(token)))
    assert result[0]["status"] == status
    assert json.loads(result[1]["body"]) == {"error": code}
    assert b"private" not in result[1]["body"] and b"secret" not in result[1]["body"]


def test_capacity_and_delivery_contention_never_claim_operation_not_started(config, store):
    token = login(config)

    async def run():
        held = False
        entered, release = asyncio.Event(), asyncio.Event()
        owner = None

        async def own():
            async with guard.serial:
                entered.set()
                await release.wait()

        async def app(scope, receive, send):
            nonlocal held, owner
            if not held:
                owner = asyncio.create_task(own())
                await entered.wait()
                held = True
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"computed"})

        observations = Observations()
        guard = Guard(app, config, Settings(), observations)
        try:
            result = await call(guard, request(token))
            assert held  # Handler completed; only final admission was contended.
            assert json.loads(result[1]["body"]) == {"error": "display_busy"}
            assert observations.entries[-1]["display_phase"] == "deliver"
        finally:
            release.set()
            if owner is not None:
                await owner
        assert (await call(guard, request(token)))[0]["status"] == 200
        first, second = object(), object()
        await guard.display_limiter.acquire_on_behalf_of(first)
        await guard.display_limiter.acquire_on_behalf_of(second)
        try:
            result = await call(guard, request(token))
            assert json.loads(result[1]["body"]) == {"error": "display_busy"}
            assert observations.entries[-1]["display_phase"] == "capacity"
        finally:
            guard.display_limiter.release_on_behalf_of(first)
            guard.display_limiter.release_on_behalf_of(second)
        assert (await call(guard, request(token)))[0]["status"] == 200

    asyncio.run(run())


def test_disconnected_delivery_never_starts_a_second_response(config, store):
    token, responses = login(config), []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"result"})

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        responses.append(message)
        if message["type"] == "http.response.body":
            raise BrokenPipeError("synthetic disconnected reader")

    with pytest.raises(BrokenPipeError):
        asyncio.run(Guard(app, config, Settings())(request(token), receive, send))
    assert len([r for r in responses if r["type"] == "http.response.start"]) == 1


def test_prehandler_diagnostics_keep_templates_not_private_ids():
    observed = Observations()
    observed.register([Route("/admin/vault-catalog/{vault_id}", lambda _: None, methods=["GET"])])
    observed.record(
        {"path": "/admin/vault-catalog/private-secret", "method": "GET"}, 503, 1, {"stage": "queue"}
    )
    assert observed.entries[-1]["route"] == "/admin/vault-catalog/{id}"
    observed.record({"path": "/admin/unknown-private", "method": "GET"}, 503, 1, {})
    assert observed.entries[-1]["route"] == "/other"
    assert "private" not in json.dumps(list(observed.entries))


def test_application_preserves_display_error_categories(http, monkeypatch):
    from fiber_bundle.organization import routes

    def failed(*args, **kwargs):
        raise IntegrityError("display_source_changed")

    monkeypatch.setattr(routes, "browse", failed)
    response = http.get(
        "/admin/organization", params={"client_id": "synthetic", "vault_id": "synthetic"}
    )
    assert response.status_code == 503
    assert response.json() == {"error": "display_changed"}
