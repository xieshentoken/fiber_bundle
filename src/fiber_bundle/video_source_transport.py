"""Finite retries for public short-link GET headers; no media or model retries."""

import http.client
import socket
import ssl
import time
from collections.abc import Callable

from . import article_fetch
from .config import InvalidInput


class SourceConnectionError(InvalidInput):
    def __init__(self, category: str, attempts: int, elapsed: float):
        self.diagnostic = {
            "stage": "resolve_source",
            "category": category,
            "attempts": attempts,
            "elapsed_ms": round(elapsed * 1000),
        }
        super().__init__("intake_video_source_" + category)


def category(error: Exception) -> str:
    if isinstance(error, ssl.SSLCertVerificationError):
        return "certificate_invalid"
    if isinstance(error, socket.gaierror):
        return "dns_failed"
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, ssl.SSLError):
        return "tls_interrupted"
    return "connection_interrupted"


def redirect(target: str, guard: Callable[[], None], deadline: float) -> tuple[int, str | None]:
    started = time.monotonic()
    for attempt in range(3):
        guard()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SourceConnectionError("timeout", attempt, time.monotonic() - started)
        connection = article_fetch.PublicConnection(
            "b23.tv", timeout=min(8, remaining), address_index=attempt, deadline=deadline
        )
        try:
            connection.request(
                "GET", target, headers={"User-Agent": "Mozilla/5.0", "Connection": "close"}
            )
            if connection.sock is not None:
                connection.sock.settimeout(connection.remaining())
            response = connection.getresponse()
            guard()
            return response.status, response.getheader("Location")
        except (OSError, http.client.HTTPException) as error:
            guard()
            code = category(error)
            if isinstance(error, ssl.SSLError) and not isinstance(
                error, (ssl.SSLEOFError, ssl.SSLZeroReturnError)
            ):
                raise SourceConnectionError(
                    code, attempt + 1, time.monotonic() - started
                ) from error
            if attempt == 2 or time.monotonic() >= deadline:
                raise SourceConnectionError(
                    code, attempt + 1, time.monotonic() - started
                ) from error
        finally:
            connection.close()
    raise AssertionError("unreachable")
