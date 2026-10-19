"""Loopback-only HTTP boundary, bounded bodies, safe errors and serialized delivery."""

import re
from contextlib import nullcontext
from dataclasses import dataclass

import anyio
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from fiber_bundle import database
from fiber_bundle.config import Config, InvalidInput, StorageError


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
    def __init__(self, app: ASGIApp, config: Config, settings: Settings):
        self.app, self.config, self.settings = app, config, settings
        self.serial = anyio.Lock()

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

        async def safe_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
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
            await self.app(scope, replay, safe_send)
            return
        if path == "/admin/organization/progress" and scope["method"] == "GET":
            from .organization.progress_http import respond

            await respond(self.app, self.serial, self.config, scope, replay, safe_send)
            return
        # Acquire off the event loop. The same M1 file lock covers authentication,
        # database/object operations AND bounded ASGI delivery, including revocations.
        async with self.serial:
            lock = database.locked(self.config)
            try:
                from .backup_automation.online import active as backup_active

                with database.lock_wait(0.3) if backup_active(self.config) else nullcontext():
                    await anyio.to_thread.run_sync(lock.__enter__)
            except database.LockTimeout:
                await JSONResponse(
                    {"error": "backup_snapshot_busy"}, 503, headers={"retry-after": "1"}
                )(scope, replay, safe_send)
                return
            except (StorageError, OSError):
                await JSONResponse({"error": "storage_unavailable"}, 503)(scope, replay, safe_send)
                return
            try:
                await self.app(scope, replay, safe_send)
            finally:
                lock.__exit__(None, None, None)
