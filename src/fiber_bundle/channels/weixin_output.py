"""Expert-only Weixin multipart delivery; each side effect has one durable attempt."""

import asyncio
import base64
import hashlib
import json
import secrets
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from ..config import InvalidInput
from ..expert_presentation import image_bytes, reason, table_rows
from .reply_text import caption


def chunks(text: str, limit: int = 1800) -> list[str]:
    """Bound UTF-8 message size without losing any text or splitting a code point."""
    result: list[str] = []
    current: list[str] = []
    size = 0
    for char in text:
        width = len(char.encode())
        if size + width > limit:
            result.append("".join(current))
            current, size = [], 0
        current.append(char)
        size += width
    if current:
        result.append("".join(current))
    return result


async def post(adapter: Any, module: Any, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    payload = {**payload, "base_info": module._base_info()}
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    async with (
        asyncio.timeout(30),
        adapter._send_session.post(
            adapter._base_url.rstrip("/") + "/ilink/bot/" + endpoint,
            data=encoded.encode(),
            headers=module._headers(adapter._token, encoded),
            allow_redirects=False,
        ) as response,
    ):
        raw = await response.content.read(65537)
        if not 200 <= response.status < 300 or len(raw) > 65536:
            raise InvalidInput("channel_weixin_response_unknown")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise InvalidInput("channel_weixin_response_unknown")
        return value


def accepted(value: dict[str, Any]) -> bool:
    # iLink's successful JSON responses can omit zero-valued status fields.
    return all(
        key not in value or (type(value[key]) is int and value[key] == 0)
        for key in ("ret", "errcode")
    )


def rejection_reason(value: dict[str, Any]) -> str:
    codes = [
        f"{key}={value[key]}"
        for key in ("ret", "errcode")
        if type(value.get(key)) is int and 0 < abs(value[key]) <= 2**31
    ]
    return "platform_rejected" + (":" + ",".join(codes) if codes else "")


def introduction(body: str | dict[str, Any]) -> str:
    if isinstance(body, str):
        return body
    text = body["text"]
    if not isinstance(text, str):
        raise InvalidInput("channel_message_format_invalid")
    titles: list[str] = []
    seen = set()
    for block in body["response"].get("expert_blocks", []):
        for item in block.get("media", []):
            if item.get("status") != "available" or (
                item.get("render_as") == "table" and table_rows(item)
            ):
                continue
            if item["sha256"] not in seen:
                seen.add(item["sha256"])
                titles.append(f"{len(titles) + 1}. {caption(item)}")
    return text + ("\n\n相关图表：\n" + "\n".join(titles) if titles else "")


def cdn_route(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "novac2c.cdn.weixin.qq.com"
        or parsed.port not in {None, 443}
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise InvalidInput("channel_weixin_upload_route_invalid")


async def upload(adapter: Any, module: Any, owner: str, raw: bytes, recheck: Any) -> dict[str, Any]:
    aes_key, filekey = secrets.token_bytes(16), secrets.token_hex(16)
    ciphertext = module._aes128_ecb_encrypt(raw, aes_key)
    await asyncio.to_thread(recheck)
    value = await post(
        adapter,
        module,
        "getuploadurl",
        {
            "filekey": filekey,
            "media_type": module.MEDIA_IMAGE,
            "to_user_id": owner,
            "rawsize": len(raw),
            "rawfilemd5": hashlib.md5(raw).hexdigest(),
            "filesize": len(ciphertext),
            "no_need_thumb": True,
            "aeskey": aes_key.hex(),
        },
    )
    if not accepted(value):
        raise InvalidInput("image_upload_failed")
    target = value.get("upload_full_url")
    if not target:
        param = value.get("upload_param")
        if not isinstance(param, str) or not param:
            raise InvalidInput("image_upload_failed")
        target = module._cdn_upload_url(adapter._cdn_base_url, param, filekey)
    if not isinstance(target, str):
        raise InvalidInput("image_upload_failed")
    cdn_route(target)
    await asyncio.to_thread(recheck)
    # No upstream send_document/send_image: they may retry ambiguous sends. Upload only once.
    async with (
        asyncio.timeout(120),
        adapter._send_session.post(
            target,
            data=ciphertext,
            headers={"Content-Type": "application/octet-stream"},
            allow_redirects=False,
        ) as response,
    ):
        if not 200 <= response.status < 300:
            raise InvalidInput("image_upload_failed")
        param = response.headers.get("x-encrypted-param")
        if not isinstance(param, str) or not param or len(param) > 8192:
            raise InvalidInput("image_upload_unknown")
    return {
        "type": module.ITEM_IMAGE,
        "image_item": {
            "media": {
                "encrypt_query_param": param,
                "aes_key": base64.b64encode(aes_key.hex().encode()).decode(),
                "encrypt_type": 1,
            },
            "mid_size": len(ciphertext),
        },
    }


async def send(
    adapter: Any,
    module: Any,
    owner: str,
    body: str | dict[str, Any],
    context: str,
    guard: Any,
    preflight: Any,
) -> bool:
    if not context or len(context) > 8192:
        raise InvalidInput("channel_weixin_context_unavailable")
    if not isinstance(body, str) and (
        not isinstance(body, dict) or body.get("schema") != "fiber-expert-channel-output/v1"
    ):
        raise InvalidInput("channel_message_format_invalid")
    number = 0

    def recheck() -> None:
        preflight(adapter)
        guard.recheck()

    def receipt(key: str, kind: str, state: str, why: str = "") -> None:
        guard.part(key, kind, state, why)

    async def message(item: dict[str, Any], kind: str) -> bool:
        nonlocal number
        number += 1
        key = str(number)
        await asyncio.to_thread(recheck)
        await asyncio.to_thread(receipt, key, kind, "sending")
        try:
            result = await post(
                adapter,
                module,
                "sendmessage",
                {
                    "msg": {
                        "from_user_id": "",
                        "to_user_id": owner,
                        "client_id": "fiber-channel-" + uuid4().hex,
                        "message_type": 2,
                        "message_state": 2,
                        "item_list": [item],
                        "context_token": context,
                    }
                },
            )
            if any(key in result and type(result[key]) is not int for key in ("ret", "errcode")):
                raise InvalidInput("channel_weixin_response_unknown")
        except Exception:
            await asyncio.to_thread(receipt, key, kind, "unknown", "message_result_unknown")
            guard.delivery_state = "unknown"
            return False
        success = accepted(result)
        await asyncio.to_thread(
            receipt,
            key,
            kind,
            "sent" if success else "failed",
            "" if success else rejection_reason(result),
        )
        guard.delivery_state = "sent" if success else "failed"
        return success

    async def text(value: str) -> bool:
        for chunk in chunks(value):
            if not await message({"type": 1, "text_item": {"text": chunk}}, "text"):
                return False
        return True

    async with adapter._send_text_gate:
        await asyncio.to_thread(preflight, adapter)
        await asyncio.to_thread(guard)
        if not await text(introduction(body)):
            return False
        if isinstance(body, str):
            return True
        seen: set[tuple[str, str, str]] = set()
        images: set[str] = set()
        for block in body["response"].get("expert_blocks", []):
            for item in block.get("media", []):
                identity = (block["module_id"], block["version"], item["resource_id"])
                if identity in seen:
                    continue
                seen.add(identity)
                title = caption(item)
                if item.get("status") != "available":
                    if not await text(f"{title}：{reason(item)}。"):
                        return False
                    continue
                if item.get("render_as") == "table" and (rows := table_rows(item)):
                    if not await text(title + "\n" + "\n".join(" | ".join(row) for row in rows)):
                        return False
                    continue
                raw = image_bytes(item)
                if item["sha256"] in images:
                    continue
                images.add(item["sha256"])
                key = "upload-" + str(number + 1)
                await asyncio.to_thread(recheck)
                await asyncio.to_thread(receipt, key, "upload", "sending")
                try:
                    image = await upload(adapter, module, owner, raw, recheck)
                except Exception as error:
                    # No sendmessage was made: a text fallback cannot duplicate an image message.
                    why = (
                        str(error)
                        if isinstance(error, InvalidInput)
                        and str(error) in {"image_upload_failed", "image_upload_unknown"}
                        else "image_upload_unknown"
                    )
                    await asyncio.to_thread(receipt, key, "upload", "failed", why)
                    await asyncio.to_thread(recheck)  # Revocation still blocks fallback.
                    if not await text(f"{title}：{reason({'reason': why})}。"):
                        return False
                    continue
                await asyncio.to_thread(receipt, key, "upload", "uploaded")
                if not await message(image, "image"):
                    if guard.delivery_state == "unknown":
                        return False
                    if not await text(f"{title}：图片发送被拒绝，已有文字保留。"):
                        return False
        return True
