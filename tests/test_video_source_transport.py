import ssl
import time

import pytest

from fiber_bundle import article_fetch
from fiber_bundle import video_source_transport as transport
from fiber_bundle.config import InvalidInput


def install(monkeypatch, errors):
    attempts, closed = [], []

    class Connection:
        sock = None

        def __init__(self, host, timeout, **kwargs):
            attempts.append(kwargs["address_index"])

        def request(self, *args, **kwargs):
            error = errors.pop(0)
            if error:
                raise error

        def getresponse(self):
            return type("Reply", (), {"status": 302, "getheader": lambda s, k: "safe"})()

        def close(self):
            closed.append(True)

    monkeypatch.setattr(article_fetch, "PublicConnection", Connection)
    return attempts, closed


def test_tls_eof_recovers_next_address(monkeypatch):
    attempts, closed = install(monkeypatch, [ssl.SSLEOFError(), None])
    assert transport.redirect("/", lambda: None, time.monotonic() + 30) == (302, "safe")
    assert attempts == [0, 1] and len(closed) == 2


def test_persistent_failure_is_finite_and_diagnostic_safe(monkeypatch):
    attempts, closed = install(monkeypatch, [TimeoutError("secret")] * 3)
    with pytest.raises(transport.SourceConnectionError) as caught:
        transport.redirect("/", lambda: None, time.monotonic() + 30)
    assert caught.value.diagnostic["attempts"] == 3
    assert caught.value.diagnostic["category"] == "timeout"
    assert "secret" not in str(caught.value.diagnostic)
    assert len(attempts) == len(closed) == 3


def test_certificate_error_is_not_retried(monkeypatch):
    attempts, closed = install(monkeypatch, [ssl.SSLCertVerificationError()])
    with pytest.raises(transport.SourceConnectionError, match="certificate_invalid"):
        transport.redirect("/", lambda: None, time.monotonic() + 30)
    assert len(attempts) == len(closed) == 1


def test_expired_deadline_does_not_connect(monkeypatch):
    attempts, _ = install(monkeypatch, [])
    with pytest.raises(transport.SourceConnectionError, match="timeout"):
        transport.redirect("/", lambda: None, time.monotonic() - 1)
    assert not attempts


def test_cancel_after_error_prevents_retry(monkeypatch):
    attempts, closed = install(monkeypatch, [TimeoutError()])

    def guard():
        if attempts:
            raise InvalidInput("intake_user_aborted")

    with pytest.raises(InvalidInput, match="aborted"):
        transport.redirect("/", guard, time.monotonic() + 30)
    assert len(attempts) == len(closed) == 1
