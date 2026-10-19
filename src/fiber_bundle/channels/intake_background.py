"""An owned intake task: slow work cannot end or block the message receiver loop."""

import asyncio
import time
from contextlib import suppress
from typing import Any

from .. import database, feishu_runtime
from ..config import Config
from . import diagnostics, intake_repository, intake_runtime, legacy, store


async def heartbeat(path: Any) -> None:
    while True:
        with suppress(TimeoutError):
            await asyncio.wait_for(asyncio.Event().wait(), timeout=2)
        feishu_runtime.write(path, {"state": "processing", "observed_at": time.time()})


async def serve(config: Config, identity: str, adapter: Any) -> None:
    channel, _ = intake_repository.channel(config, identity)
    home = (
        legacy.home(config, channel)
        if channel.get("origin") == "legacy"
        else store.home(config, identity)
    )
    path = home / "intake-status.json"
    busy = 0
    while True:
        delay = 1
        progress = asyncio.create_task(heartbeat(path))
        try:
            state = await intake_runtime.tick(config, identity, adapter)
            busy = 0
            feishu_runtime.write(path, {"state": state, "observed_at": time.time()})
        except asyncio.CancelledError:
            raise
        except database.LockTimeout:
            # A lease timeout must not permanently stop intake. Committed operation
            # states still gate re-entry; in-flight/unknown network work is never reset.
            busy += 1
            delay = min(2 ** min(busy, 4), 15)
            feishu_runtime.write(
                path,
                {
                    "state": "waiting_storage",
                    "error": "intake_storage_busy",
                    "observed_at": time.time(),
                    "retry_in_seconds": delay,
                },
            )
        except Exception as error:
            # Existing operation ledgers retain interrupted/unknown work. Do not
            # replay it automatically; the receiver remains connected while this
            # owned task waits for explicit restart and is joined on shutdown.
            progress.cancel()
            await asyncio.gather(progress, return_exceptions=True)
            feishu_runtime.write(
                path,
                {
                    "state": "needs_review",
                    "observed_at": time.time(),
                    **diagnostics.failure(error),
                },
            )
            await asyncio.Future[None]()
        finally:
            progress.cancel()
            # Only the child's cancellation becomes a result. A cancellation of
            # this receiver task must propagate, including during heartbeat join.
            await asyncio.gather(progress, return_exceptions=True)
        await asyncio.sleep(delay)
