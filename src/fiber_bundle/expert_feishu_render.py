"""One-attempt rich Feishu output; uploads fail per item, message ambiguity never retries."""

import importlib
import io
import json
import re
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from .channels.reply_text import caption
from .expert_presentation import image_bytes, reason, table_rows
from .feishu_delivery import response_evidence


def send(
    client: Any, target: str, packet: dict[str, Any], guard: Callable[[], None]
) -> dict[str, str]:
    models = importlib.import_module("lark_oapi.api.im.v1")
    # This guard consumes the existing durable single-send claim before the first side effect.
    guard()
    recheck = getattr(guard, "recheck", lambda: None)
    content = [[{"tag": "text", "text": packet["text"]}]]
    uploads: dict[str, str | None] = {}
    upload_reasons: dict[str, str] = {}
    for block in packet["response"].get("expert_blocks", []):
        for item in block.get("media", []):
            content.append([{"tag": "text", "text": caption(item)}])
            if item.get("status") != "available":
                content.append([{"tag": "text", "text": reason(item)}])
                continue
            if item.get("render_as") == "table" and (rows := table_rows(item)):
                # Feishu post has no table element: keep verified rows as explicit text.
                content.append(
                    [
                        {
                            "tag": "text",
                            "text": "表格内容：\n" + "\n".join(" | ".join(row) for row in rows),
                        }
                    ]
                )
                continue
            key = item["sha256"]
            upload_reason = "image_upload_failed"
            if key not in uploads:
                recheck()
                try:
                    raw = image_bytes(item)
                    request = (
                        models.CreateImageRequest.builder()
                        .request_body(
                            models.CreateImageRequestBody.builder()
                            .image_type("message")
                            .image(io.BytesIO(raw))
                            .build()
                        )
                        .build()
                    )
                    response = client.im.v1.image.create(request)
                    image_key = getattr(getattr(response, "data", None), "image_key", None)
                    uploads[key] = (
                        image_key
                        if response is not None
                        and response.success()
                        and (
                            isinstance(image_key, str)
                            and re.fullmatch(r"[A-Za-z0-9_-]{1,400}", image_key)
                        )
                        else None
                    )
                    if response is None:
                        upload_reason = "image_upload_unknown"
                except Exception:
                    uploads[key] = None
                    upload_reason = "image_upload_unknown"
                upload_reasons[key] = upload_reason
            if uploads[key]:
                content.append([{"tag": "img", "image_key": uploads[key]}])
            else:
                content.append([{"tag": "text", "text": reason({"reason": upload_reasons[key]})}])
    request = (
        models.CreateMessageRequest.builder()
        .receive_id_type("open_id")
        .request_body(
            models.CreateMessageRequestBody.builder()
            .receive_id(target)
            .msg_type("post")
            .content(
                json.dumps(
                    {"zh_cn": {"title": "知识库查询", "content": content}}, ensure_ascii=False
                )
            )
            .uuid(str(uuid4()))
            .build()
        )
        .build()
    )
    recheck()
    try:
        response = client.im.v1.message.create(request)
        return response_evidence(response)
    except Exception:
        return {"category": "transport_exception"}
