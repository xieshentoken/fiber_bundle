"""Receive-only network recovery; never repeat capture, model calls or sends."""

import asyncio
import re
from collections.abc import Callable
from typing import Any

from ..config import InvalidInput

DELAYS = (2, 4, 8, 16, 30)


def transient(error: Exception, module: Any) -> bool:
    if isinstance(error, (TimeoutError, ConnectionError)):
        return True
    http = (
        re.fullmatch(r"iLink POST ilink/bot/getupdates HTTP (\d{3}):[\s\S]*", str(error))
        if isinstance(error, RuntimeError)
        else None
    )
    if http:
        status = int(http[1])
        return status in {408, 429} or 500 <= status <= 599
    client = getattr(module, "aiohttp", None)
    certificates = tuple(
        kind
        for name in ("ClientConnectorCertificateError", "ClientConnectorSSLError")
        if isinstance(kind := getattr(client, name, None), type)
    )
    if certificates and isinstance(error, certificates):
        return False
    types = tuple(
        kind
        for name in ("ClientConnectionError", "ClientPayloadError")
        if isinstance(kind := getattr(client, name, None), type)
    )
    return bool(types and isinstance(error, types))


async def receive(
    adapter: Any, module: Any, cursor: str, recheck: Callable[[], None]
) -> dict[str, Any]:
    for attempt in range(len(DELAYS) + 1):
        # Keep receiver heartbeat responsive while a current-scope check waits on storage.
        await asyncio.to_thread(recheck)  # These failures are never network retries.
        try:
            response = await module._get_updates(
                adapter._poll_session,
                base_url=adapter._base_url,
                token=adapter._token,
                sync_buf=cursor,
                timeout_ms=35000,
            )
        except Exception as error:
            if not transient(error, module):
                raise
            if attempt == len(DELAYS):
                raise InvalidInput("channel_weixin_poll_exhausted") from error
            adapter._fiber_poll_state = "reconnecting"
            await asyncio.sleep(DELAYS[attempt])
            continue
        if not isinstance(response, dict):
            raise InvalidInput("channel_weixin_response_invalid")
        # iLink may omit zero-valued fields. Present values are strict integers.
        if any(
            key in response and (type(response[key]) is not int or response[key] != 0)
            for key in ("ret", "errcode")
        ):
            raise InvalidInput("channel_weixin_poll_rejected")
        messages = response.get("msgs", [])
        if (
            not isinstance(messages, list)
            or len(messages) > 100
            or any(not isinstance(message, dict) for message in messages)
        ):
            raise InvalidInput("channel_weixin_response_invalid")
        token = response.get("get_updates_buf", cursor)
        if not isinstance(token, str) or len(token) > 60000:
            raise InvalidInput("channel_weixin_response_invalid")
        adapter._fiber_poll_state = "connected"
        return response
    raise AssertionError("unreachable")
