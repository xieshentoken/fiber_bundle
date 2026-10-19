"""Pinned iLink file protocol candidate: allocate once, encrypted upload once, send once."""

import asyncio
import base64
import hashlib
import json
import secrets
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import uuid4

from . import weixin_receiver as receiver
from .config import InvalidInput
from .inputs import AssetInput
from .weixin_hermes_sender import context


def _route(value: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise InvalidInput("document_transport_route_invalid")
    return parsed.scheme, parsed.hostname, parsed.port


async def _json(response: Any) -> dict[str, Any]:
    raw = bytearray()
    async for chunk in response.content.iter_chunked(8192):
        raw.extend(chunk)
        if len(raw) > 65536:
            raise InvalidInput("document_transport_response_limit")
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError):
        raise InvalidInput("document_transport_response_invalid") from None
    if (
        not isinstance(result, dict)
        or type(result.get("ret")) is not int
        or result["ret"] != 0
        or type(result.get("errcode", 0)) is not int
        or result.get("errcode", 0) != 0
    ):
        raise InvalidInput("document_transport_not_acknowledged")
    return result


async def send_once(
    adapter: Any,
    owner: str,
    asset: AssetInput,
    authorize: Callable[[str], None],
    *,
    headers: Callable[[str, str], dict[str, str]],
    base_info: Callable[[], dict[str, Any]],
) -> bool:
    """Never call Hermes send_document/_send_file: those re-open paths and may log/retry."""
    descriptor = asset.descriptor()
    data = asset.data
    if not isinstance(data, bytes):
        raise InvalidInput("document_transport_asset_invalid")
    if (
        descriptor["original_name"] not in {"draft.md", "draft.txt"}
        or not 0 < len(data) <= 8 * 1024 * 1024
    ):
        raise InvalidInput("document_transport_asset_invalid")
    async with asyncio.timeout(60), adapter._send_text_gate:
        _, binding = receiver.preflight(adapter)
        if (
            owner != binding.owner_id
            or getattr(adapter, "_fiber_capture_version", None) != 1
            or not adapter._running
            or not adapter._send_session
            or not adapter._token
        ):
            raise InvalidInput("document_reviewed_sender_required")
        api_origin = _route(adapter._base_url)
        cdn_origin = _route(adapter._cdn_base_url)
        if urlsplit(adapter._base_url).query or urlsplit(adapter._cdn_base_url).query:
            raise InvalidInput("document_transport_route_invalid")
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        key = secrets.token_bytes(16)
        padding = 16 - len(data) % 16
        encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        ciphertext = encryptor.update(data + bytes([padding]) * padding) + encryptor.finalize()
        filekey = secrets.token_hex(16)
        ctx = context(adapter, owner)

        async def post(endpoint: str, value: dict[str, Any], stage: str) -> dict[str, Any]:
            context(adapter, owner)
            authorize(stage)
            url = adapter._base_url.rstrip("/") + "/ilink/bot/" + endpoint
            if _route(url) != api_origin:
                raise InvalidInput("document_transport_api_changed")
            body = json.dumps(
                {**value, "base_info": base_info()}, ensure_ascii=False, separators=(",", ":")
            )
            async with adapter._send_session.post(
                url,
                data=body.encode(),
                headers=headers(adapter._token, body),
                allow_redirects=False,
            ) as response:
                if not 200 <= response.status < 300:
                    raise InvalidInput("document_transport_http_failed")
                return await _json(response)

        allocation = await post(
            "getuploadurl",
            {
                "filekey": filekey,
                "media_type": 3,
                "to_user_id": owner,
                "rawsize": len(data),
                "rawfilemd5": hashlib.md5(data).hexdigest(),
                "filesize": len(ciphertext),
                "no_need_thumb": True,
                "aeskey": key.hex(),
            },
            "allocate",
        )
        upload = allocation.get("upload_full_url")
        if not upload:
            param = allocation.get("upload_param")
            if not isinstance(param, str) or not 0 < len(param) <= 8192:
                raise InvalidInput("document_transport_upload_unavailable")
            upload = (
                adapter._cdn_base_url.rstrip("/")
                + "/upload?upload_param="
                + quote(param, safe="")
                + "&filekey="
                + filekey
            )
        if not isinstance(upload, str) or len(upload) > 16384 or _route(upload) != cdn_origin:
            raise InvalidInput("document_transport_cdn_changed")
        context(adapter, owner)
        authorize("upload")
        async with adapter._send_session.post(
            upload,
            data=ciphertext,
            headers={"Content-Type": "application/octet-stream"},
            allow_redirects=False,
        ) as response:
            if response.status != 200:
                raise InvalidInput("document_transport_upload_failed")
            param = response.headers.get("x-encrypted-param")
            if not isinstance(param, str) or not 0 < len(param) <= 8192:
                raise InvalidInput("document_transport_upload_not_acknowledged")
            count = 0
            async for chunk in response.content.iter_chunked(8192):
                count += len(chunk)
                if count > 65536:
                    raise InvalidInput("document_transport_response_limit")
        ctx = context(adapter, owner)
        await post(
            "sendmessage",
            {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": owner,
                    "client_id": "fiber-document-" + uuid4().hex,
                    "message_type": 2,
                    "message_state": 2,
                    "context_token": ctx,
                    "item_list": [
                        {
                            "type": 4,
                            "file_item": {
                                "media": {
                                    "encrypt_query_param": param,
                                    "aes_key": base64.b64encode(key.hex().encode()).decode(),
                                    "encrypt_type": 1,
                                },
                                "file_name": descriptor["original_name"],
                                "len": str(len(data)),
                            },
                        }
                    ],
                }
            },
            "send",
        )
        return True
