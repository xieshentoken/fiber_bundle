"""Validate trusted-adapter input; forwarded identities never authorize an event."""

import json
from dataclasses import dataclass

from .config import InvalidInput
from .inputs import text
from .objects import digest

MAX_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class Binding:
    account_id: str
    owner_id: str
    client_id: str
    vault_id: str

    def validate(self) -> None:
        for value in (self.account_id, self.owner_id, self.client_id, self.vault_id):
            text(value, limit=400)
        if self.account_id == self.owner_id:
            raise InvalidInput("weixin self binding refused")


@dataclass(frozen=True)
class Event:
    event_id: str
    raw: bytes
    media: dict[str, bytes]
    state: str


def message_id(value: object) -> str:
    """Use a bounded decimal key for iLink integers; retain the original JSON unchanged."""
    if type(value) is int:
        if abs(value) >= 10**400:
            raise InvalidInput("invalid weixin message id")
        value = str(value)
    return text(value, limit=400)


def encoded(value: object) -> bytes:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError):
        raise InvalidInput("invalid weixin event JSON") from None
    if len(raw) > MAX_BYTES:
        raise InvalidInput("weixin event too large")
    return raw


def requirements(items: list[object]) -> tuple[set[str], bool]:
    required: set[str] = set()
    unsupported = not items

    def item(value: object, key: str, *, reference: bool = False) -> None:
        nonlocal unsupported
        if not isinstance(value, dict):
            unsupported = True
            return
        kind = value.get("type")
        if type(kind) is int and kind == 1:
            content = value.get("text_item")
            if (
                not isinstance(content, dict)
                or not isinstance(content.get("text"), str)
                or not content["text"].strip()
            ):
                unsupported = True
        elif type(kind) is int and kind in (2, 3, 4, 5):
            required.add(key)
        else:
            unsupported = True
        if value.get("ref_msg") is not None:
            ref = value["ref_msg"]
            if reference or not isinstance(ref, dict) or "message_item" not in ref:
                unsupported = True
            else:
                item(ref["message_item"], key + ".ref", reference=True)

    for index, value in enumerate(items):
        item(value, str(index))
    return required, unsupported


def prepare(
    binding: Binding, response: object, media: dict[str, dict[str, bytes]]
) -> tuple[str, str, list[Event], int]:
    """Media keys are item indices (e.g. '0', '1.ref'); bytes come from a trusted downloader."""
    binding.validate()
    raw = encoded(response)
    if not isinstance(response, dict):
        raise InvalidInput("invalid weixin response")
    for key in ("ret", "errcode"):
        value = response.get(key, 0)
        if value is not None and (type(value) is not int or value != 0):
            raise InvalidInput("weixin poll failed")
    cursor = response.get("get_updates_buf")
    if not isinstance(cursor, str) or len(cursor) > 8192:
        raise InvalidInput("invalid weixin cursor")
    messages = response.get("msgs")
    if not isinstance(messages, list) or len(messages) > 100:
        raise InvalidInput("invalid weixin messages")
    if not isinstance(media, dict):
        raise InvalidInput("invalid weixin media")
    events: list[Event] = []
    ignored = 0
    total = len(raw)
    for message in messages:
        if not isinstance(message, dict):
            raise InvalidInput("invalid weixin message")
        # Exact envelope identity, never nicknames or nested forwarded-message fields.
        if (
            message.get("from_user_id") != binding.owner_id
            or message.get("to_user_id") != binding.account_id
            or message.get("room_id")
            or message.get("chat_room_id")
        ):
            ignored += 1
            continue
        event_id = message_id(message.get("message_id"))
        items = message.get("item_list")
        if not isinstance(items, list) or len(items) > 100:
            raise InvalidInput("invalid weixin items")
        required, unsupported = requirements(items)
        supplied = media.get(event_id, {})
        if not isinstance(supplied, dict) or not set(supplied) <= required:
            raise InvalidInput("unexpected weixin media")
        for value in supplied.values():
            if not isinstance(value, bytes) or not value or len(value) > MAX_BYTES:
                raise InvalidInput("invalid weixin media bytes")
            total += len(value)
        if total > 2 * MAX_BYTES:
            raise InvalidInput("weixin poll too large")
        state = (
            "unsupported"
            if unsupported
            else "incomplete"
            if set(supplied) != required
            else "pending"
        )
        events.append(Event(event_id, encoded(message), dict(supplied), state))
    if not set(media) <= {event.event_id for event in events}:
        raise InvalidInput("media without authorized event")
    return cursor, digest(raw), events, ignored
