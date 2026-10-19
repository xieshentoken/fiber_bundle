"""Bounded sequential worker for explicit media attempts; fail-stop, no retry loop."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from . import auth, weixin_download, weixin_media
from . import weixin_media_queue as queue
from .config import Config, InvalidInput
from .weixin_events import Binding


async def dispatch(
    config: Config,
    binding: Binding,
    *,
    max_items: int = 1,
    fetcher: Callable[[str], Awaitable[bytes]] = weixin_download.fetch,
    attempt_id: str | None = None,
) -> dict[str, Any]:
    if type(max_items) is not int or not 1 <= max_items <= 100:
        raise InvalidInput("media worker limit must be between 1 and 100")
    if attempt_id is not None and (not isinstance(attempt_id, str) or max_items != 1):
        raise InvalidInput("explicit media attempt requires a single-item worker")
    results: list[dict[str, Any]] = []
    with queue.worker_lock(config):
        for _ in range(max_items):
            task = queue.claim(config, binding, attempt_id=attempt_id)
            if task is None:
                return {"state": "idle", "attempts": results}
            try:
                queue.inspect_claim(config, binding, task["id"])
                await weixin_media.recover_item(
                    config, binding, task["job_id"], task["item_key"], fetcher=fetcher
                )
            except asyncio.CancelledError:
                # If permission/storage is unavailable, leave running for explicit reconciliation.
                try:
                    queue.finish(config, binding, task["id"], "interrupted", "worker_interrupted")
                except Exception:
                    pass
                raise
            except Exception:
                try:
                    queue.finish(config, binding, task["id"], "failed", "download_failed")
                    results.append(queue.status(config, binding, task["id"]))
                except auth.Denied:
                    return {
                        "state": "blocked",
                        "attempts": results,
                        "unsettled_attempt": task["id"],
                    }
                return {"state": "stopped", "attempts": results}
            # Settlement failure leaves running for reconciliation, not a fake download failure.
            queue.finish(config, binding, task["id"], "succeeded", None)
            results.append(queue.status(config, binding, task["id"]))
    return {"state": "limit_reached", "attempts": results}
