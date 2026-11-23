import gzip
import io
import socket
import ssl
import time
from email.message import Message

import pytest

from fiber_bundle import article_fetch as network
from fiber_bundle.vendor.article_parser import CaptureError


@pytest.mark.parametrize(
    "data,encoding,limit",
    [
        (gzip.compress(b"a" * 10000), "gzip", 100),
        (gzip.compress(b"abc")[:-3], "gzip", 100),
        (gzip.compress(b"abc") + gzip.compress(b"def"), "gzip", 100),
        (b"abc", "br", 100),
        (b"abcd", "identity", 3),
        (b"", "", 100),
    ],
)
def test_bounded_decoding_rejects_bombs_partial_and_unknown(data, encoding, limit):
    with pytest.raises(network.FetchError):
        network.decode_body(data, encoding, limit)


def test_bounded_gzip():
    assert network.decode_body(gzip.compress(b"abc"), "gzip", 3) == b"abc"


def test_mixed_public_and_private_dns_never_connects(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ],
    )

    def forbidden(*args, **kwargs):
        pytest.fail("must reject mixed DNS before opening socket")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    with pytest.raises(CaptureError):
        network.PublicConnection("public.example").connect()


def test_public_socket_is_pinned_and_tls_retains_hostname(monkeypatch):
    connections, tls = [], []
    transport = object()
    monkeypatch.setattr(network, "resolve_public_addresses", lambda host: ["8.8.8.8"])
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda address, timeout: connections.append((address, timeout)) or transport,
    )

    class Context:
        def wrap_socket(self, raw, server_hostname):
            tls.append((raw, server_hostname))
            return transport

    monkeypatch.setattr(network.ssl, "create_default_context", Context)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1234")
    network.PublicConnection("public.example", timeout=20).connect()
    assert connections == [(("8.8.8.8", 443), 20)]
    assert tls == [(transport, "public.example")]


class Reply(io.BytesIO):
    def __init__(self, status, body, headers):
        super().__init__(body)
        self.status = status
        self.headers = Message()
        for k, v in headers:
            self.headers[k] = v

    def getheader(self, key, default=None):
        return self.headers.get(key, default)

    def isclosed(self):
        return self.closed


def install(monkeypatch, replies):
    calls = []
    closed = []

    class Connection:
        def __init__(self, host, timeout, **kwargs):
            self.sock = None
            self.host = host

        def request(self, method, target, headers):
            calls.append((self.host, method, target, headers))

        def getresponse(self):
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        def close(self):
            closed.append(self.host)

    monkeypatch.setattr(network, "PublicConnection", Connection)
    return calls, closed


def test_redirect_to_private_address_rejected(monkeypatch):
    calls, closed = install(monkeypatch, [Reply(302, b"", [("Location", "https://127.0.0.1/")])])
    with pytest.raises(network.FetchError):
        network.fetch_public("https://public.example/", 100)
    assert len(calls) == len(closed) == 1


def test_redirect_without_credentials_or_cookies(monkeypatch):
    calls, closed = install(
        monkeypatch,
        [
            Reply(302, b"", [("Location", "https://cdn.example/body"), ("Set-Cookie", "secret=x")]),
            Reply(200, b"abc", [("Content-Type", "text/html"), ("Content-Length", "3")]),
        ],
    )
    result = network.fetch_public("https://public.example/", 100)
    assert result.data == b"abc" and result.url == "https://cdn.example/body"
    assert [call[0] for call in calls] == ["public.example", "cdn.example"]
    assert len(closed) == 2
    assert all("Cookie" not in c[3] and "Authorization" not in c[3] for c in calls)


@pytest.mark.parametrize(
    "status,body,headers",
    [
        (206, b"abc", []),
        (429, b"private response", []),
        (200, b"abc", [("Content-Length", "4")]),
        (200, b"abc", [("Content-Length", "3"), ("Content-Length", "3")]),
        (200, b"abc", [("Content-Range", "bytes 0-2/100")]),
        (200, b"a" * 101, []),
    ],
)
def test_partial_oversized_or_http_error_closes_connection(monkeypatch, status, body, headers):
    calls, closed = install(monkeypatch, [Reply(status, body, headers)])
    with pytest.raises(network.FetchError) as failure:
        network.fetch_public("https://public.example/", 100)
    assert "private response" not in str(failure.value)
    assert len(calls) == len(closed) == 1


def test_redirects_are_bounded(monkeypatch):
    calls, closed = install(
        monkeypatch, [Reply(302, b"", [("Location", "/again")]) for _ in range(6)]
    )
    with pytest.raises(network.FetchError, match="redirect_limit"):
        network.fetch_public("https://public.example/", 100)
    assert len(calls) == len(closed) == 6


def image_fetch(**kwargs):
    options = {
        "validate": lambda _: None,
        "guard": lambda: None,
        "deadline": time.monotonic() + 10,
        **kwargs,
    }
    return network.fetch_image("https://cdn.example/image?private=opaque", 100, **options)


@pytest.mark.parametrize(
    "headers,body,expected",
    [
        (b"Content-Length: 100\r\n", b"a" * 100, b"a" * 100),
        (b"Transfer-Encoding: chunked\r\n", b"4\r\ndata\r\n0\r\n\r\n", b"data"),
        (b"", b"data", b"data"),
        (b"Content-Length: 5\r\n", b"data", None),
    ],
)
def test_image_get_handles_close_delimited_socket_ownership(monkeypatch, headers, body, expected):
    requests = []

    class Connection(network.PublicConnection):
        def connect(self):
            transport, server = socket.socketpair()
            with server:
                server.sendall(
                    b"HTTP/1.1 200 OK\r\nConnection: close\r\n" + headers + b"\r\n" + body
                )
                server.shutdown(socket.SHUT_WR)
                transport.settimeout(self.remaining())
                self.sock = transport
                # Keep the read end open for the actual request write.
                self.server = server.dup()
            requests.append(transport)

        def close(self):
            super().close()
            if hasattr(self, "server"):
                self.server.close()

    monkeypatch.setattr(network, "PublicConnection", Connection)
    monkeypatch.setattr(network.time, "sleep", lambda _: None)
    if expected is None:
        with pytest.raises(network.ImageFetchError, match="incomplete_response") as error:
            image_fetch()
        assert error.value.diagnostic["attempts"] == 3
    else:
        assert image_fetch().data == expected
        assert len(requests) == 1
    assert all(transport.fileno() == -1 for transport in requests)


def test_image_retry_resolves_each_hop_and_uses_next_address(monkeypatch):
    calls, closed = install(
        monkeypatch,
        [ConnectionResetError(), Reply(200, b"ok", [("Content-Length", "2")])],
    )
    create = network.PublicConnection
    addresses, checked, delays = [], [], []

    def connection(*args, **kwargs):
        addresses.append(kwargs["address_index"])
        return create(*args, **kwargs)

    monkeypatch.setattr(network, "PublicConnection", connection)
    monkeypatch.setattr(network.time, "sleep", delays.append)
    result = image_fetch(validate=checked.append)
    assert result.data == b"ok"
    assert addresses == [0, 1]
    assert len(calls) == len(closed) == len(checked) == 2
    assert delays == [0.25]


def test_image_retry_honors_retry_after_and_keeps_diagnostic_private(monkeypatch):
    limited = Reply(429, b"private response", [("Retry-After", "2")])
    unavailable = Reply(503, b"private response", [("Retry-After", "120")])
    calls, closed = install(monkeypatch, [limited, unavailable])
    delays = []
    monkeypatch.setattr(network.time, "sleep", delays.append)

    with pytest.raises(network.ImageFetchError) as failure:
        image_fetch()

    error = failure.value
    assert error.code == "intake_article_image_http_unavailable"
    assert error.retryable
    assert error.diagnostic == {
        "stage": "fetch_article_image",
        "category": "http_unavailable",
        "attempts": 2,
        "elapsed_ms": error.diagnostic["elapsed_ms"],
        "http_status": 503,
    }
    assert "private" not in str(error) and "private" not in repr(error.diagnostic)
    assert delays == [2]
    assert len(calls) == len(closed) == 2
    assert limited.closed and unavailable.closed


@pytest.mark.parametrize(
    ("reply", "code"),
    [
        (ssl.SSLCertVerificationError("private certificate detail"), "certificate_invalid"),
        (socket.gaierror(socket.EAI_NONAME, "private hostname"), "dns_failed"),
        (Reply(403, b"private response", []), "http_error"),
        (Reply(200, b"abc", [("Content-Length", "101")]), "size_invalid"),
    ],
)
def test_image_fetch_does_not_retry_permanent_failures(monkeypatch, reply, code):
    calls, closed = install(monkeypatch, [reply])
    monkeypatch.setattr(network.time, "sleep", lambda _: pytest.fail("unexpected retry"))
    with pytest.raises(network.ImageFetchError, match="intake_article_image_" + code) as error:
        image_fetch()
    assert error.value.diagnostic["attempts"] == 1
    assert not error.value.retryable
    assert len(calls) == len(closed) == 1


def test_image_fetch_stops_after_three_attempts(monkeypatch):
    calls, closed = install(monkeypatch, [TimeoutError()] * 3)
    delays = []
    monkeypatch.setattr(network.time, "sleep", delays.append)
    with pytest.raises(network.ImageFetchError, match="intake_article_image_timeout") as error:
        image_fetch()
    assert error.value.diagnostic["attempts"] == 3
    assert error.value.retryable
    assert len(calls) == len(closed) == 3
    assert delays == [0.25, 0.5]


def test_image_fetch_does_not_cross_deadline_or_retry_after_cancel(monkeypatch):
    from fiber_bundle.config import InvalidInput

    calls, closed = install(monkeypatch, [TimeoutError()])
    delayed = []

    def guard():
        if delayed:
            raise InvalidInput("intake_user_aborted")

    monkeypatch.setattr(network.time, "sleep", delayed.append)
    with pytest.raises(InvalidInput, match="intake_user_aborted"):
        image_fetch(guard=guard)
    assert len(calls) == len(closed) == 1

    with pytest.raises(network.ImageFetchError, match="intake_article_image_timeout") as error:
        image_fetch(deadline=time.monotonic() - 1)
    assert error.value.diagnostic["attempts"] == 0
    assert error.value.retryable
    assert len(calls) == len(closed) == 1


def test_image_body_rechecks_shared_deadline(monkeypatch):
    now = 0.0

    class SlowReply(Reply):
        def read1(self, size):
            nonlocal now
            now = 11
            return super().read1(size)

    calls, closed = install(monkeypatch, [SlowReply(200, b"abc", [])])
    monkeypatch.setattr(network.time, "monotonic", lambda: now)
    monkeypatch.setattr(network.time, "sleep", lambda _: pytest.fail("unexpected retry"))
    with pytest.raises(network.ImageFetchError, match="intake_article_image_timeout") as error:
        image_fetch(deadline=10)
    assert error.value.diagnostic["attempts"] == 1
    assert len(calls) == len(closed) == 1
