"""Buffer expensive display reads outside the lease; reauthorize before delivery.

Only explicitly audited GET projections use this path. The storage generation and
observed source files must still match under the delivery lease. Nothing is cached
across sessions or reused by preview, confirmation, query execution or writeback.
"""

import sqlite3
from pathlib import Path
from typing import Any

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import auth, database, display_observation
from .config import Config, IntegrityError, InvalidInput, StorageError
from .display_dependencies import stamp
from .http_admission import dispatch

PATHS = frozenset(
    {
        "/admin/organization",
        "/admin/vault-placements",
        "/admin/vault-placements/mocs",
        "/admin/knowledge-graph/notes",
        "/admin/note-index/status",
    }
)


async def respond(
    app: Any,
    serial: Any,
    limiter: Any,
    config: Config,
    scope: Any,
    receive: Any,
    send: Any,
    timing: dict[str, Any],
) -> None:
    request = Request(scope)

    async def admitted(handler: Any) -> None:
        await dispatch(
            handler, serial, config, scope, receive, send, timing, busy_code="display_busy"
        )

    before = None
    messages: list[Any] = []
    observations: dict[Path, tuple[int, ...]] = {}
    authorizations: dict[tuple[str, str, str], Any] = {}
    delivery_started = False

    async def error(code: str, status: int = 503) -> None:
        await JSONResponse({"error": code}, status, headers={"retry-after": "1"})(
            scope, receive, send
        )

    async def prepare(*_: Any) -> None:
        nonlocal before
        timing["display_phase"] = "prepare"
        before = await anyio.to_thread.run_sync(stamp, config, request)

    async def deliver(*_: Any) -> None:
        nonlocal delivery_started
        # Authorization is checked again, including expiry, even if no file changed.
        try:
            after = await anyio.to_thread.run_sync(stamp, config, request)
        except InvalidInput as exc:
            # The request was valid at preparation. A newly invalid/revoked source
            # policy is a changed observation, never a reason to deliver old data.
            raise IntegrityError("display_source_changed") from exc
        if before != after:
            timing["stage"] = "display_changed"
            await error("display_changed")
            return
        try:
            await anyio.to_thread.run_sync(
                display_observation.verify_authorizations, config, authorizations
            )
            await anyio.to_thread.run_sync(display_observation.verify, observations)
        except FileNotFoundError:
            timing["stage"] = "display_changed"
            await error("display_changed")
            return
        from .note_search_jobs import changed

        for vault_id in pending_indexes:
            await anyio.to_thread.run_sync(changed, config, vault_id)
        for message in messages:
            delivery_started = True
            await send(message)

    pending_indexes: set[str] = set()
    size = 0

    async def capture(message: Any) -> None:
        nonlocal size
        size += len(message.get("body", b""))
        if size > 32 * 1024 * 1024:
            raise IntegrityError("display_response_limit")
        messages.append(message)

    acquired = False
    try:
        timing["display_phase"] = "capacity"
        try:
            with anyio.fail_after(1):
                await limiter.acquire()
            acquired = True
        except TimeoutError:
            timing["stage"] = "display_capacity"
            await error("display_busy")
            return
        await admitted(prepare)
        if before is None:
            return
        token = display_observation.current.set(observations)
        pending_token = display_observation.pending_indexes.set(pending_indexes)
        authorization_token = display_observation.authorizations.set(authorizations)
        try:
            timing["display_phase"] = "compute"
            await app(scope, receive, capture)
        finally:
            display_observation.current.reset(token)
            display_observation.pending_indexes.reset(pending_token)
            display_observation.authorizations.reset(authorization_token)
        timing["display_phase"] = "deliver"
        await admitted(deliver)
    except auth.Denied as exc:
        await error("request_denied", exc.status)
    except (StorageError, OSError, sqlite3.Error) as exc:
        if delivery_started:
            raise  # A disconnected response cannot be replaced with another HTTP response.
        code = display_observation.error_code(exc) or (
            "display_busy"
            if isinstance(exc, database.LockTimeout)
            else "invalid_input"
            if isinstance(exc, InvalidInput)
            else "storage_unavailable"
        )
        timing["stage"] = code
        await error(code, 422 if code == "invalid_input" else 503)
    finally:
        if acquired:
            limiter.release()
