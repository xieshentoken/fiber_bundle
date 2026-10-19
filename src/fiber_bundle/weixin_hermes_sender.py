"""One bounded text POST using pinned Hermes headers and the journal's owner context."""

import asyncio
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from . import weixin_inbox as inbox
from . import weixin_receiver as receiver
from .config import IntegrityError, InvalidInput
from .objects import digest

MAX_RESPONSE = 65536


def context(adapter: Any, owner: str) -> str:
    config, binding = receiver.preflight(adapter)
    if owner != binding.owner_id:
        raise InvalidInput("outbound owner mismatch")
    with inbox.connection(config, binding) as db:
        inbox.bound(db, binding)
        row = db.execute(
            "SELECT raw,raw_sha256 FROM events WHERE account_id=? ORDER BY rowid DESC LIMIT 1",
            (binding.account_id,),
        ).fetchone()
        if row is None:
            raise InvalidInput("outbound context unavailable")
        if digest(bytes(row["raw"])) != row["raw_sha256"]:
            raise IntegrityError("outbound context digest mismatch")
        message = json.loads(row["raw"])
        if (
            message.get("from_user_id") != owner
            or message.get("to_user_id") != binding.account_id
            or message.get("room_id")
            or message.get("chat_room_id")
        ):
            raise IntegrityError("outbound context binding mismatch")
        token = message.get("context_token")
        if not isinstance(token, str) or not token.strip() or len(token) > 8192:
            raise InvalidInput("outbound context unavailable")
        return token


async def send_once(
    adapter: Any,
    owner: str,
    text: str,
    *,
    headers: Callable[[str, str], dict[str, str]],
    base_info: Callable[[], dict[str, Any]],
    authorize: Callable[[], None] | None = None,
) -> bool:
    """Only explicit integer-zero API acknowledgement is success. Never retry/fallback."""
    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 8192:
        raise InvalidInput("invalid outbound text")
    async with asyncio.timeout(30), adapter._send_text_gate:
        if (
            getattr(adapter, "_fiber_capture_version", None) != 1
            or not adapter._running
            or not adapter._send_session
            or not adapter._token
        ):
            raise InvalidInput("reviewed connected sender required")
        route = urlsplit(adapter._base_url)
        if (
            route.scheme != "https"
            or not route.hostname
            or route.username
            or route.password
            or route.query
            or route.fragment
        ):
            raise InvalidInput("invalid outbound API route")
        context_token = context(adapter, owner)  # Recheck grants after waiting for the gate.
        if authorize is not None:
            authorize()  # Task results recheck cancellation and evidence after the send gate.
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": owner,
                "client_id": "fiber-weixin-" + uuid4().hex,
                "message_type": 2,
                "message_state": 2,
                "item_list": [{"type": 1, "text_item": {"text": text}}],
                "context_token": context_token,
            },
            "base_info": base_info(),
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        async with adapter._send_session.post(
            adapter._base_url.rstrip("/") + "/ilink/bot/sendmessage",
            data=body.encode("utf-8"),
            headers=headers(adapter._token, body),
            allow_redirects=False,
        ) as response:
            if not 200 <= response.status < 300:
                return False
            raw = bytearray()
            async for chunk in response.content.iter_chunked(8192):
                raw.extend(chunk)
                if len(raw) > MAX_RESPONSE:
                    return False
            try:
                result = json.loads(raw)
            except (ValueError, UnicodeError):
                return False
            return (
                isinstance(result, dict)
                and type(result.get("ret")) is int
                and result["ret"] == 0
                and ("errcode" not in result or type(result["errcode"]) is int)
                and result.get("errcode", 0) == 0
            )
