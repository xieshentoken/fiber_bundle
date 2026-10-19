"""Owned synchronous work with finite storage waits, outside the receiver event loop."""

import asyncio
from collections.abc import Callable

from . import database


async def run[**P, T](function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    def invoke() -> T:
        with database.lock_wait(1):
            return function(*args, **kwargs)

    task = asyncio.create_task(asyncio.to_thread(invoke))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Stop owns and joins work already started; no detached writer survives cancellation.
        try:
            await task
        finally:
            raise
