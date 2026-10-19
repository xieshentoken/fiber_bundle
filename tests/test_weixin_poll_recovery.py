"""Recovery is bounded and applies only to the idempotent receive request."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from fiber_bundle.channels import diagnostics, health, weixin_poll
from fiber_bundle.config import InvalidInput


def runtime(outcomes):
    calls = []
    adapter = SimpleNamespace(
        _poll_session=None,
        _base_url="https://synthetic.invalid",
        _token="SECRET",
        _fiber_poll_state="connected",
    )

    async def fetch(*args, **kwargs):
        calls.append(kwargs["sync_buf"])
        value = outcomes.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    return adapter, SimpleNamespace(_get_updates=fetch), calls


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("SECRET"),
        ConnectionResetError("SECRET"),
        RuntimeError("iLink POST ilink/bot/getupdates HTTP 503: SECRET"),
    ],
)
def test_transient_receive_recovers_using_same_cursor(monkeypatch, error):
    adapter, module, calls = runtime([error, {"msgs": []}])
    waits, checks = [], []

    async def wait(delay):
        assert adapter._fiber_poll_state == "reconnecting"
        waits.append(delay)

    monkeypatch.setattr(weixin_poll.asyncio, "sleep", wait)
    result = asyncio.run(weixin_poll.receive(adapter, module, "cursor", lambda: checks.append(1)))
    assert result == {"msgs": []}
    assert calls == ["cursor", "cursor"]
    assert len(checks) == 2 and waits == [2]
    assert adapter._fiber_poll_state == "connected"


def test_retries_exhaust_and_diagnostic_never_leaks_response(monkeypatch):
    adapter, module, calls = runtime([ConnectionError("SECRET")] * 6)
    waits = []

    async def wait(delay):
        waits.append(delay)

    monkeypatch.setattr(weixin_poll.asyncio, "sleep", wait)
    with pytest.raises(InvalidInput, match="channel_weixin_poll_exhausted") as caught:
        asyncio.run(weixin_poll.receive(adapter, module, "cursor", lambda: None))
    assert len(calls) == 6 and waits == [2, 4, 8, 16, 30]
    report = diagnostics.failure(caught.value)
    assert report["error"] == "channel_weixin_poll_exhausted"
    assert report["diagnostic"]["locations"]
    assert "SECRET" not in json.dumps(report)


@pytest.mark.parametrize(
    "value",
    [
        RuntimeError("iLink POST ilink/bot/getupdates HTTP 401: SECRET"),
        RuntimeError("iLink POST ilink/bot/getupdates HTTP 403: SECRET"),
        RuntimeError("unrecognized SECRET"),
        ValueError("SECRET"),
        {"ret": -14},
        {"ret": "0"},
        {"msgs": [None]},
        {"msgs": {}},
        {"get_updates_buf": None},
    ],
)
def test_rejections_and_malformed_responses_never_retry(value):
    adapter, module, calls = runtime([value])
    with pytest.raises((RuntimeError, ValueError, InvalidInput)):
        asyncio.run(weixin_poll.receive(adapter, module, "cursor", lambda: None))
    assert calls == ["cursor"]


def test_permission_withdrawn_during_backoff_never_fetches_again(monkeypatch):
    adapter, module, calls = runtime([ConnectionError("SECRET")])
    allowed = [True]

    async def wait(delay):
        allowed[0] = False

    def check():
        if not allowed[0]:
            raise InvalidInput("withdrawn")

    monkeypatch.setattr(weixin_poll.asyncio, "sleep", wait)
    with pytest.raises(InvalidInput, match="withdrawn"):
        asyncio.run(weixin_poll.receive(adapter, module, "cursor", check))
    assert calls == ["cursor"]


def test_cancellation_interrupts_backoff(monkeypatch):
    adapter, module, calls = runtime([ConnectionError("SECRET")])

    async def wait(delay):
        raise asyncio.CancelledError()

    monkeypatch.setattr(weixin_poll.asyncio, "sleep", wait)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(weixin_poll.receive(adapter, module, "cursor", lambda: None))
    assert calls == ["cursor"]


def test_reconnecting_transport_is_visible():
    assert (
        health.transport(
            {"process_state": "running", "heartbeat_fresh": True, "state": "reconnecting"}
        )
        == "connecting"
    )
