"""Owned synchronous work with finite storage waits, outside the receiver event loop."""

import asyncio
from collections.abc import Callable

from . import database


async def run[**P, T](function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    return await _owned(1, function, *args, **kwargs)


async def delivery[**P, T](function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Keep an admitted single send alive across finite local checkpoint contention."""
    return await _owned(60, function, *args, **kwargs)


async def _owned[**P, T](
    seconds: float, function: Callable[P, T], *args: P.args, **kwargs: P.kwargs
) -> T:
    def invoke() -> T:
        with database.lock_wait(seconds):
            return function(*args, **kwargs)

    task = asyncio.create_task(asyncio.to_thread(invoke))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        # Stop owns and joins work already started; no detached writer survives cancellation.
        # Repeated cancellation must not cancel the Task wrapping the still-running thread.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        try:
            task.result()
        except BaseException as error:
            # A late storage failure must not turn shutdown into ordinary intake recovery.
            raise cancelled from error
        raise cancelled
