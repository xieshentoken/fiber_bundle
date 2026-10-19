"""Bound progress-read waits while retaining authorization and delivery under the lease."""

from typing import Any

import anyio
from starlette.responses import JSONResponse

from .. import database
from ..config import StorageError


async def respond(app: Any, serial: Any, config: Any, scope: Any, receive: Any, send: Any) -> None:
    async def busy() -> None:
        await JSONResponse(
            {"error": "organization_progress_busy"}, 503, headers={"retry-after": "1"}
        )(scope, receive, send)

    try:
        with anyio.fail_after(0.25):
            await serial.acquire()
    except TimeoutError:
        await busy()
        return
    try:
        lock = database.locked(config)
        try:
            with database.lock_wait(0.25):
                await anyio.to_thread.run_sync(lock.__enter__)
        except database.LockTimeout:
            await busy()
            return
        except (StorageError, OSError):
            await JSONResponse({"error": "storage_unavailable"}, 503)(scope, receive, send)
            return
        try:
            await app(scope, receive, send)
        finally:
            lock.__exit__(None, None, None)
    finally:
        serial.release()
