"""Loopback-only HTTP boundary, bounded bodies, safe errors and serialized delivery."""

import re
import time
from collections.abc import Awaitable
from dataclasses import dataclass

import anyio
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from fiber_bundle.config import Config, InvalidInput
from fiber_bundle.http_observation import Observations


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8765
    session_ttl: int = 3600
    max_body_bytes: int = 8 * 1024 * 1024
    max_original_bytes: int = 8 * 1024 * 1024
    login_attempts: int = 5
    login_window: int = 60

    def validate(self) -> None:
        if self.host != "127.0.0.1":
            raise InvalidInput("only IPv4 loopback binding is supported")
        for key, low, high in (
            ("port", 1, 65535),
            ("session_ttl", 1, 86400),
            ("max_body_bytes", 1024, 64 * 1024 * 1024),
            ("max_original_bytes", 1, 64 * 1024 * 1024),
            ("login_attempts", 1, 100),
            ("login_window", 1, 3600),
        ):
            value = getattr(self, key)
            if type(value) is not int or not low <= value <= high:
                raise InvalidInput("invalid server setting")

    @property
    def authority(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def origin(self) -> str:
        return f"http://{self.authority}"


class Guard:
    def __init__(
        self,
        app: ASGIApp,
        config: Config,
        settings: Settings,
        observations: Observations | None = None,
        lifecycle: object = None,
    ):
        self.app, self.config, self.settings = app, config, settings
        self.lifecycle = lifecycle
        self.serial = anyio.Lock()
        self.display_limiter = anyio.CapacityLimiter(2)
        self.control_limiter = anyio.CapacityLimiter(4)
        self.observations = observations or Observations()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = scope["headers"]
        hosts = [v.decode("latin1") for k, v in headers if k == b"host"]
        origins = [v.decode("latin1") for k, v in headers if k == b"origin"]
        peer = scope.get("client")
        path = scope["path"]
        mutation = scope["method"] not in {"GET", "HEAD", "OPTIONS"}
        status = 0
        if hosts != [self.settings.authority] or (peer and peer[0] != "127.0.0.1"):
            status = 400
        elif origins and origins != [self.settings.origin]:
            status = 403
        elif path.startswith("/admin/") and mutation and origins != [self.settings.origin]:
            status = 403
        elif scope["method"] == "OPTIONS":
            status = 405
        if status:
            await JSONResponse({"error": "request_denied"}, status)(scope, receive, send)
            return
        lengths = [v for k, v in headers if k == b"content-length"]
        if lengths:
            try:
                if len(lengths) != 1 or int(lengths[0]) < 0:
                    raise ValueError
                if int(lengths[0]) > self.settings.max_body_bytes:
                    status = 413
            except ValueError:
                status = 400
        body = bytearray()
        if not status:
            try:
                with anyio.fail_after(10):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        body.extend(message.get("body", b""))
                        if len(body) > self.settings.max_body_bytes:
                            status = 413
                            break
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                status = 408
        if status:
            await JSONResponse({"error": "invalid_request_body"}, status)(scope, receive, send)
            return

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        started = time.monotonic()
        timing: dict[str, object] = {}
        response_status = 0

        async def safe_send(message: Message) -> None:
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
                elapsed = (time.monotonic() - started) * 1000
                queue = timing.get("queue_ms", 0)
                storage = timing.get("storage_ms", 0)
                message["headers"] = list(message.get("headers", [])) + [
                    (
                        b"server-timing",
                        (
                            f"queue;dur={queue}, storage;dur={storage}, total;dur={elapsed:.1f}"
                        ).encode(),
                    ),
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (
                        b"content-security-policy",
                        b"default-src 'self'; script-src 'self'; "
                        b"style-src 'self'; img-src 'self' data:; connect-src 'self'; "
                        b"frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                    ),
                ]
            with anyio.fail_after(10):
                await send(message)

        async def observe(operation: Awaitable[None]) -> None:
            try:
                await operation
            finally:
                self.observations.record(scope, response_status, time.monotonic() - started, timing)

        # Public code/static health data has no storage/authentication dependency.
        if scope["method"] in {"GET", "HEAD"} and (
            path in {"/health", "/"} or path.startswith("/static/")
        ):
            await observe(self.app(scope, replay, safe_send))
            return
        # This endpoint checks authorization under its own short leases per chunk.
        # Holding the outer lease across a slow stream would block revocation/imports.
        if (
            path == "/admin/backup-automation"
            or path.startswith("/admin/backup-automation/")
            or (path == "/admin/vault-relocations" or path.startswith("/admin/vault-relocations/"))
            or path.startswith("/admin/vault-merges/")
            or (
                scope["method"] == "POST"
                and re.fullmatch(
                    r"/admin/channels/[^/]+/services/(?:activate|(?:query|intake)/(?:start|pause|update))",
                    path,
                )
            )
            or (
                scope["method"] == "POST"
                and re.fullmatch(r"/admin/agent-connections/[^/]+/knowledge-expert", path)
            )
            or (
                scope["method"] == "POST"
                and path
                in {
                    "/admin/note-index/update",
                    "/admin/knowledge-graph/runtime/detect",
                    "/admin/vault-catalog/preview",
                    "/v1/agent-knowledge/expert",
                }
            )
            or (
                scope["method"] == "GET"
                and re.fullmatch(
                    r"/admin/(?:documents/[^/]+/source|imports/[^/]+/(?:source|expert-query))",
                    path,
                )
            )
        ):
            await observe(self.app(scope, replay, safe_send))
            return
        if (
            scope["method"] == "GET"
            and path
            in {
                "/admin/organization/progress",
                "/admin/backend",
                "/admin/vaults",
                "/admin/clients",
                "/admin/session",
            }
        ) or (
            scope["method"] == "POST"
            and re.fullmatch(r"/admin/organization/maintenance/[a-fA-F0-9-]{36}/pause", path)
        ):
            from .admin_control_http import respond as control

            await observe(
                control(
                    self.config,
                    self.control_limiter,
                    scope,
                    bytes(body),
                    replay,
                    safe_send,
                    timing,
                    self.lifecycle,
                )
            )
            return
        from .display_http import PATHS, respond

        if scope["method"] == "GET" and path in PATHS:
            await observe(
                respond(
                    self.app,
                    self.serial,
                    self.display_limiter,
                    self.config,
                    scope,
                    replay,
                    safe_send,
                    timing,
                )
            )
            return
        from .http_admission import dispatch

        await observe(
            dispatch(self.app, self.serial, self.config, scope, replay, safe_send, timing)
        )
