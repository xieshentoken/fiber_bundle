"""Feishu SDK events mapped to the shared archival envelope, with original provenance.

The legacy journal uses iLink-shaped item indices internally. This mapping is explicitly
marked as Feishu; it never claims that archive members are original WeChat messages.
"""

import json
from typing import Any
from urllib.parse import urlsplit

from .config import InvalidInput
from .inputs import text
from .weixin_events import Binding, encoded


def normalize(
    binding: Binding, payload: dict[str, Any], *, commands: bool = False
) -> dict[str, Any] | None:
    binding.validate()
    if not binding.account_id.startswith("feishu:"):
        raise InvalidInput("explicit Feishu binding required")
    encoded(payload)
    event = payload.get("event", {})
    if not isinstance(event, dict):
        raise InvalidInput("invalid Feishu event")
    sender, message = event.get("sender", {}), event.get("message", {})
    if not isinstance(sender, dict) or not isinstance(message, dict):
        raise InvalidInput("invalid Feishu envelope")
    header = payload.get("header", {})
    if not isinstance(header, dict):
        raise InvalidInput("invalid Feishu header")
    if header.get("app_id") not in (None, binding.account_id.removeprefix("feishu:")):
        raise InvalidInput("Feishu application mismatch")
    # A callback verification token is transport authentication, never source material.
    original = {
        **payload,
        "header": {key: value for key, value in header.items() if key != "token"},
    }
    identity = sender.get("sender_id", {})
    if (
        not isinstance(identity, dict)
        or identity.get("open_id") != binding.owner_id
        or sender.get("sender_type") != "user"
        or message.get("chat_type") != "p2p"
    ):
        return None
    event_id = text(message.get("message_id"), limit=400)
    text(message.get("chat_id"), limit=400)
    content = message.get("content")
    if not isinstance(content, str):
        raise InvalidInput("invalid Feishu content")
    try:
        body = json.loads(content)
    except (ValueError, TypeError):
        raise InvalidInput("invalid Feishu content JSON") from None
    if not isinstance(body, dict):
        raise InvalidInput("invalid Feishu content object")
    kind = message.get("message_type")
    items: list[dict[str, Any]] = []
    files: dict[str, dict[str, str]] = {}
    card_kind = None

    def add_text(value: object) -> None:
        if isinstance(value, str) and value.strip():
            items.append({"type": 1, "text_item": {"text": value}})

    def add_file(key: object, name: object, resource: str) -> None:
        key = text(key, limit=512)
        name = text(name, limit=400)
        if name in {".", ".."} or any(c in name for c in "/\\"):
            raise InvalidInput("invalid Feishu attachment name")
        index = str(len(items))
        files[index] = {"key": key, "name": name, "resource": resource}
        items.append({"type": 2 if resource == "image" else 4})

    if kind == "text":
        add_text(body.get("text"))
    elif kind == "file":
        add_file(body.get("file_key"), body.get("file_name"), "file")
    elif kind == "image":
        add_file(body.get("image_key"), "image.jpg", "image")
    elif kind == "post":
        # Locales are alternatives, not distinct source messages. Prefer Chinese.
        post = body if "content" in body else body.get("zh_cn", body.get("en_us"))
        if isinstance(post, dict) and isinstance(post.get("content"), list):
            add_text(post.get("title"))
            for row in post["content"]:
                if not isinstance(row, list):
                    raise InvalidInput("invalid Feishu post row")
                for element in row:
                    if not isinstance(element, dict):
                        raise InvalidInput("invalid Feishu post element")
                    tag = element.get("tag")
                    if tag == "text":
                        add_text(element.get("text"))
                    elif tag == "a":
                        add_text(element.get("href"))
                    elif tag == "img":
                        add_file(element.get("image_key"), "image.jpg", "image")
                    else:
                        items.append({"type": 99})  # Preserve unknown parts as unsupported.
    elif kind == "interactive":
        links: list[str] = []

        def card_links(value: object, depth: int = 0) -> None:
            if depth > 12:
                raise InvalidInput("Feishu card nesting limit exceeded")
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {"url", "href"} and isinstance(child, str):
                        parsed = urlsplit(child)
                        if (
                            parsed.scheme in {"https", "http"}
                            and parsed.hostname
                            and not parsed.username
                            and not parsed.password
                            and child not in links
                        ):
                            links.append(child)
                    card_links(child, depth + 1)
            elif isinstance(value, list):
                for child in value:
                    card_links(child, depth + 1)
            if len(links) > 100:
                raise InvalidInput("too many Feishu card links")

        card_links(body)
        for link in links:
            add_text(link)
        if not links and "视频号" in json.dumps(body, ensure_ascii=False):
            card_kind = "wechat_channels"
            add_text("微信视频号分享卡片：来源已接收，正文与字幕待补全。")
    if not items:
        items.append({"type": 99})
    result = {
        "message_id": event_id,
        "from_user_id": binding.owner_id,
        "to_user_id": binding.account_id,
        "forwarded": not commands or kind != "text",  # Shared cards/files remain source data.
        "item_list": items,
        "fiber_feishu": {
            "schema": "fiber-feishu-source/v1",
            "raw_event": original,
            "files": files,
            **({"card_kind": card_kind, "content_state": "card_only"} if card_kind else {}),
            "original_chat_sender": None,
            "original_chat_sent_at": None,
            "original_chat_message_count": None,
            "platform_completeness": "not_verified",
        },
    }
    encoded(result)
    return result
