import gzip
import io
import socket
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


def install(monkeypatch, replies):
    calls = []
    closed = []

    class Connection:
        def __init__(self, host, timeout):
            self.host = host

        def request(self, method, target, headers):
            calls.append((self.host, method, target, headers))

        def getresponse(self):
            return replies.pop(0)

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
