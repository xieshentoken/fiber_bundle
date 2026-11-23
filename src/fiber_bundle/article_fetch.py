"""Bounded public HTTPS fetches for the fixed article worker, never a host proxy."""

from __future__ import annotations

import errno
import http.client
import socket
import ssl
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urljoin, urlsplit

from .articles import ArticleError, public_url
from .vendor.article_parser import CaptureError, resolve_public_addresses


class FetchError(ArticleError):
    """Safe error code; remote bodies and arbitrary response headers stay private."""

    def __init__(
        self, message: str, *, status: int | None = None, retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class ImageFetchError(ArticleError):
    """A failed image GET with a bounded diagnostic suitable for the intake record."""

    def __init__(
        self,
        category: str,
        attempts: int,
        elapsed: float,
        *,
        status: int | None = None,
        retryable: bool = False,
    ) -> None:
        code = "intake_article_image_" + category
        super().__init__(code, code=code)
        self.retryable = retryable
        self.diagnostic = {
            "stage": "fetch_article_image",
            "category": category,
            "attempts": attempts,
            "elapsed_ms": round(elapsed * 1000),
            **({"http_status": status} if status is not None else {}),
        }


@dataclass(frozen=True)
class Response:
    url: str
    content_type: str
    data: bytes
    redirects: tuple[str, ...] = ()


def _remaining(deadline: float, timeout: float = 20) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("public_connection_deadline")
    return min(timeout, remaining)


class PublicConnection(http.client.HTTPSConnection):
    """Resolve once, reject mixed private DNS, pin the socket, retain TLS hostname checks."""

    def __init__(
        self,
        host: str,
        timeout: float = 20,
        *,
        address_index: int = 0,
        deadline: float | None = None,
    ) -> None:
        super().__init__(host, timeout=timeout)
        self.connection_timeout = timeout
        self.address_index = address_index
        self.deadline = deadline

    def remaining(self) -> float:
        if self.deadline is None:
            return self.connection_timeout
        return _remaining(self.deadline, self.connection_timeout)

    def connect(self) -> None:
        addresses = resolve_public_addresses(self.host)
        transport = socket.create_connection(
            (addresses[self.address_index % len(addresses)], 443), timeout=self.remaining()
        )
        try:
            if self.deadline is not None:
                transport.settimeout(self.remaining())
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


def _retry_after(value: str | None) -> float | None:
    if value is None or len(value) > 128:
        return None
    value = value.strip()
    if value.isascii() and value.isdecimal():
        return float(value)
    try:
        date = parsedate_to_datetime(value)
        return max(0, date.timestamp() - time.time()) if date.tzinfo else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _read_body(
    response: http.client.HTTPResponse,
    limit: int,
    *,
    deadline: float | None,
    transport: socket.socket | None,
    guard: Callable[[], None] | None,
) -> bytes:
    if deadline is None:
        return response.read(limit + 1)
    result = bytearray()
    next_guard = time.monotonic()
    # read1 closes its file when the final fixed-length bytes arrive. With
    # Connection: close the response may also own the last reference to the socket.
    while len(result) <= limit and not response.isclosed():
        now = time.monotonic()
        if guard is not None and now >= next_guard:
            guard()
            next_guard = now + 0.25
        remaining = _remaining(deadline)
        if transport is not None:
            transport.settimeout(remaining)
        chunk = response.read1(min(64 * 1024, limit + 1 - len(result)))
        _remaining(deadline)
        if not chunk:
            break
        result.extend(chunk)
    return bytes(result)


def fetch_public(
    url: str,
    limit: int,
    *,
    validate: Callable[[str], None] | None = None,
    address_index: int = 0,
    deadline: float | None = None,
    guard: Callable[[], None] | None = None,
) -> Response:
    """One GET per redirect hop, no cookies/credentials/proxies or automatic retries."""
    if type(limit) is not int or not 1 <= limit <= 12 * 1024 * 1024:
        raise FetchError("invalid_byte_limit")
    try:
        current = public_url(url)
        chain = []
        for _ in range(6):
            if guard is not None:
                guard()
            if validate is not None:
                validate(current)
            chain.append(current)
            parsed = urlsplit(current)
            assert parsed.hostname is not None
            connection = PublicConnection(
                parsed.hostname,
                timeout=_remaining(deadline) if deadline is not None else 20,
                address_index=address_index,
                deadline=deadline,
            )
            target = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
            if parsed.query:
                target += "?" + quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
            response = None
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
                # HTTPConnection may clear its socket on a close-delimited response.
                transport = connection.sock if deadline is not None else None
                if deadline is not None:
                    remaining = _remaining(deadline)
                    if transport is not None:
                        transport.settimeout(remaining)
                response = connection.getresponse()
                if deadline is not None:
                    _remaining(deadline)
                if response.status in (301, 302, 303, 307, 308):
                    location = response.getheader("Location")
                    if not location:
                        raise FetchError("redirect_without_location")
                    current = public_url(urljoin(current, location))
                    continue
                if response.status != 200:
                    raise FetchError(
                        f"http_{response.status}",
                        status=response.status,
                        retry_after=_retry_after(response.getheader("Retry-After"))
                        if response.status in {429, 503}
                        else None,
                    )
                if response.getheader("Content-Range"):
                    raise FetchError("partial_response")
                lengths = response.headers.get_all("Content-Length", [])
                if len(lengths) > 1 or (lengths and not lengths[0].isascii()):
                    raise FetchError("invalid_content_length")
                if lengths and (not lengths[0].isdigit() or int(lengths[0]) > limit):
                    raise FetchError("response_too_large")
                data = _read_body(
                    response, limit, deadline=deadline, transport=transport, guard=guard
                )
                if len(data) > limit:
                    raise FetchError("response_too_large")
                if lengths and len(data) != int(lengths[0]):
                    raise FetchError("incomplete_response")
                data = decode_body(data, response.getheader("Content-Encoding", "").lower(), limit)
                if guard is not None:
                    guard()
                if deadline is not None:
                    _remaining(deadline)
                return Response(current, response.getheader("Content-Type", ""), data, tuple(chain))
            finally:
                try:
                    if response is not None:
                        response.close()
                finally:
                    connection.close()
    except FetchError:
        raise
    except (ArticleError, CaptureError, OSError, ValueError, http.client.HTTPException) as exc:
        raise FetchError("public_https_failed") from exc
    raise FetchError("redirect_limit")


def _image_failure(error: FetchError) -> tuple[str, bool]:
    if error.status == 429:
        return "rate_limited", True
    if error.status in {408, 500, 502, 503, 504}:
        return "http_unavailable", True
    if error.status is not None:
        return "http_error", False
    if str(error) == "incomplete_response":
        return "incomplete_response", True
    if str(error) in {"response_too_large", "response_too_large_or_empty"}:
        return "size_invalid", False
    if str(error) != "public_https_failed":
        return "response_invalid", False

    cause = error.__cause__
    if isinstance(cause, CaptureError) and isinstance(cause.__cause__, socket.gaierror):
        cause = cause.__cause__
    if isinstance(cause, socket.gaierror):
        return "dns_failed", cause.errno == socket.EAI_AGAIN
    if isinstance(cause, ssl.SSLCertVerificationError):
        return "certificate_invalid", False
    if isinstance(cause, (ssl.SSLEOFError, ssl.SSLZeroReturnError)):
        return "tls_interrupted", True
    if isinstance(cause, ssl.SSLError):
        return "tls_failed", False
    if isinstance(cause, TimeoutError):
        return "timeout", True
    if isinstance(cause, http.client.IncompleteRead):
        return "incomplete_response", True
    if isinstance(cause, ConnectionError) or (
        isinstance(cause, OSError)
        and cause.errno in {errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ETIMEDOUT}
    ):
        return "connection_interrupted", True
    if isinstance(cause, (ArticleError, CaptureError)):
        return "source_invalid", False
    return "connection_failed", False


def fetch_image(
    url: str,
    limit: int,
    *,
    validate: Callable[[str], None],
    guard: Callable[[], None],
    deadline: float,
) -> Response:
    """Retry only a single image GET, retaining its original absolute deadline."""
    started = time.monotonic()
    attempts = 0
    last: FetchError | None = None
    while attempts < 3:
        guard()
        if time.monotonic() >= deadline:
            raise ImageFetchError(
                "timeout", attempts, time.monotonic() - started, retryable=True
            ) from last
        attempts += 1
        try:
            return fetch_public(
                url,
                limit,
                validate=validate,
                address_index=attempts - 1,
                deadline=deadline,
                guard=guard,
            )
        except FetchError as error:
            last = error
            guard()
            category, transient = _image_failure(error)
            delay = max(0.25 * 2 ** (attempts - 1), error.retry_after or 0)
            if not transient or attempts == 3 or delay > 3 or time.monotonic() + delay >= deadline:
                raise ImageFetchError(
                    category,
                    attempts,
                    time.monotonic() - started,
                    status=error.status,
                    retryable=transient,
                ) from error
            time.sleep(delay)
    raise AssertionError("unreachable")
