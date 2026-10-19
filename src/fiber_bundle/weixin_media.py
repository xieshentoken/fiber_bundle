"""One explicitly requested original attachment download, then append-only recovery."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from . import weixin_download as download
from . import weixin_inbox as inbox
from . import weixin_recovery as recovery
from .config import Config, InvalidInput
from .weixin_events import Binding


async def recover_item(
    config: Config,
    binding: Binding,
    job_id: str,
    item_key: str,
    *,
    fetcher: Callable[[str], Awaitable[bytes]] = download.fetch,
) -> dict[str, Any]:
    """Local trusted call, not a URL endpoint or automatic receiving-side download.

    fetcher is a trusted test/integration seam, never a value from channel input.
    Network I/O is outside the DB lock; completion rechecks revocation/cancellation.
    """
    with inbox.connection(config, binding) as db:
        inbox.bound(db, binding)
        if not recovery.exists(db):
            raise InvalidInput("recovery must be initialized")
        row, present, required, state = recovery.load(db, binding, job_id)
        recovery.ensure_active(db, binding, row["event_id"])
        if not isinstance(item_key, str) or item_key not in required:
            raise InvalidInput("unexpected recovery item")
        if item_key in present:
            return {
                "job_id": job_id,
                "raw_sha256": row["raw_sha256"],
                "state": state,
                "missing_items": sorted(required - present.keys()),
                "dispatched": False,
            }
        message = json.loads(row["raw"])
        parts = item_key.split(".")
        item = message["item_list"][int(parts[0])]
        if len(parts) == 2:
            item = item["ref_msg"]["message_item"]
        source = download.source(item)
        sha = row["raw_sha256"]
    try:
        async with asyncio.timeout(30):
            wire = await fetcher(source.url)
        data = download.decode(wire, source.key)
    except download.DownloadError:
        raise
    except TimeoutError:
        raise download.DownloadError("media_download_timeout") from None
    except Exception:
        raise download.DownloadError("media_transport_failed") from None
    return recovery.complete(config, binding, job_id, sha, {item_key: data})
