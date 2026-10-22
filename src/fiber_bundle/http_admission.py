"""Bound HTTP admission without weakening the authorization-through-delivery lease."""

import time
from typing import Any

import anyio
from starlette.responses import JSONResponse

from . import database
from .config import StorageError


async def dispatch(
    app: Any,
    serial: Any,
    config: Any,
    scope: Any,
    receive: Any,
    send: Any,
    timing: dict[str, Any],
    *,
    busy_code: str = "storage_busy",
) -> None:
    async def busy(stage: str, code: str = busy_code) -> None:
        timing["stage"] = stage
        await JSONResponse({"error": code}, 503, headers={"retry-after": "1"})(scope, receive, send)

    started = time.monotonic()
    try:
        with anyio.fail_after(1):
            await serial.acquire()
    except TimeoutError:
        timing["queue_ms"] = round((time.monotonic() - started) * 1000, 1)
        await busy("queue")
        return
    timing["queue_ms"] = round((time.monotonic() - started) * 1000, 1)
    try:
        lock = database.locked(config)
        started = time.monotonic()
        from .backup_automation.online import active as backup_active

        backing_up = backup_active(config)
        try:
            with database.lock_wait(0.3 if backing_up else 1):
                await anyio.to_thread.run_sync(lock.__enter__)
        except database.LockTimeout:
            timing["storage_ms"] = round((time.monotonic() - started) * 1000, 1)
            await busy("storage", "backup_snapshot_busy" if backing_up else busy_code)
            return
        except (StorageError, OSError):
            timing["stage"] = "storage"
            await JSONResponse({"error": "storage_unavailable"}, 503)(scope, receive, send)
            return
        timing["storage_ms"] = round((time.monotonic() - started) * 1000, 1)
        try:
            await app(scope, receive, send)
        finally:
            lock.__exit__(None, None, None)
    finally:
        serial.release()
