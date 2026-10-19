"""Bounded public HTTPS fetches for the fixed article worker, never a host proxy."""

from __future__ import annotations

import http.client
import socket
import ssl
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote, urljoin, urlsplit

from .articles import ArticleError, public_url
from .vendor.article_parser import CaptureError, resolve_public_addresses


class FetchError(ArticleError):
    """Safe error code; remote bodies and arbitrary response headers stay private."""


@dataclass(frozen=True)
class Response:
    url: str
    content_type: str
    data: bytes
    redirects: tuple[str, ...] = ()


class PublicConnection(http.client.HTTPSConnection):
    """Resolve once, reject mixed private DNS, pin the socket, retain TLS hostname checks."""

    def connect(self) -> None:
        addresses = resolve_public_addresses(self.host)
        transport = socket.create_connection((addresses[0], 443), timeout=self.timeout)
        try:
            self.sock = ssl.create_default_context().wrap_socket(
                transport, server_hostname=self.host
            )
        except BaseException:
            transport.close()
            raise


def decode_body(data: bytes, encoding: str, limit: int) -> bytes:
    if encoding in ("", "identity"):
        result = data
    elif encoding == "gzip":
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            result = decoder.decompress(data, limit + 1)
        except zlib.error as exc:
            raise FetchError("invalid_compression") from exc
        if len(result) > limit or decoder.unconsumed_tail:
            raise FetchError("response_too_large")
        if not decoder.eof or decoder.unused_data:
            raise FetchError("incomplete_compression")
    else:
        raise FetchError("unsupported_content_encoding")
    if not result or len(result) > limit:
        raise FetchError("response_too_large_or_empty")
    return result


def fetch_public(
    url: str, limit: int, *, validate: Callable[[str], None] | None = None
) -> Response:
    """One GET per redirect hop, no cookies/credentials/proxies or automatic retries."""
    if type(limit) is not int or not 1 <= limit <= 12 * 1024 * 1024:
        raise FetchError("invalid_byte_limit")
    try:
        current = public_url(url)
        chain = []
        for _ in range(6):
            if validate is not None:
                validate(current)
            chain.append(current)
            parsed = urlsplit(current)
            assert parsed.hostname is not None
            connection = PublicConnection(parsed.hostname, timeout=20)
            target = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
            if parsed.query:
                target += "?" + quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
            try:
                connection.request(
                    "GET",
                    target,
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; FiberArticle/1.0)",
                        "Accept-Encoding": "gzip",
                        "Connection": "close",
                    },
                )
                response = connection.getresponse()
                if response.status in (301, 302, 303, 307, 308):
                    location = response.getheader("Location")
                    if not location:
                        raise FetchError("redirect_without_location")
                    current = public_url(urljoin(current, location))
                    continue
                if response.status != 200:
                    raise FetchError(f"http_{response.status}")
                if response.getheader("Content-Range"):
                    raise FetchError("partial_response")
                lengths = response.headers.get_all("Content-Length", [])
                if len(lengths) > 1 or (lengths and not lengths[0].isascii()):
                    raise FetchError("invalid_content_length")
                if lengths and (not lengths[0].isdigit() or int(lengths[0]) > limit):
                    raise FetchError("response_too_large")
                data = response.read(limit + 1)
                if len(data) > limit:
                    raise FetchError("response_too_large")
                if lengths and len(data) != int(lengths[0]):
                    raise FetchError("incomplete_response")
                data = decode_body(data, response.getheader("Content-Encoding", "").lower(), limit)
                return Response(current, response.getheader("Content-Type", ""), data, tuple(chain))
            finally:
                connection.close()
    except FetchError:
        raise
    except (ArticleError, CaptureError, OSError, ValueError, http.client.HTTPException) as exc:
        raise FetchError("public_https_failed") from exc
    raise FetchError("redirect_limit")
