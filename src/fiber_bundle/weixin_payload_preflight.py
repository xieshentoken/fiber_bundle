"""Offline source-shape diagnostics, without identity authorization or payload expansion."""

import hashlib
import json
from typing import Any

from .chat_documents import extract as extract_chat
from .config import InvalidInput
from .document_capture import reject_duplicate
from .weixin_events import MAX_BYTES, message_id, requirements

KINDS = {1: "text", 2: "image", 3: "voice", 4: "file", 5: "video"}


def _constant(value: str) -> None:
    raise InvalidInput("payload_json_invalid")


def _locate(
    item: object, key: str, locations: list[dict[str, Any]], *, reference: bool = False
) -> None:
    kind = item.get("type") if isinstance(item, dict) else None
    name = KINDS.get(kind, "unknown") if type(kind) is int else "unknown"
    content = item.get("text_item") if isinstance(item, dict) else None
    text = content.get("text") if isinstance(content, dict) else None
    locations.append(
        {
            "item_key": key,
            "kind": name,
            "text_bytes": len(text.encode()) if name == "text" and isinstance(text, str) else None,
            "reference": reference,
        }
    )
    if isinstance(item, dict) and item.get("ref_msg") is not None and not reference:
        ref = item["ref_msg"]
        _locate(
            ref.get("message_item") if isinstance(ref, dict) else None,
            key + ".ref",
            locations,
            reference=True,
        )


def _poll(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidInput("payload_poll_shape")
    for key in ("ret", "errcode"):
        flag = value.get(key, 0)
        if flag is not None and (type(flag) is not int or flag != 0):
            raise InvalidInput("payload_poll_failed")
    cursor, messages = value.get("get_updates_buf"), value.get("msgs")
    if not isinstance(cursor, str) or len(cursor) > 8192:
        raise InvalidInput("payload_poll_cursor")
    if not isinstance(messages, list) or len(messages) > 100:
        raise InvalidInput("payload_poll_messages")
    inspected = []
    seen: set[str] = set()
    duplicates = 0
    for ordinal, message in enumerate(messages, 1):
        if not isinstance(message, dict):
            raise InvalidInput("payload_message_shape")
        try:
            identifier = message_id(message.get("message_id"))
        except InvalidInput:
            raise InvalidInput("payload_message_id") from None
        items = message.get("item_list")
        if not isinstance(items, list) or len(items) > 100:
            raise InvalidInput("payload_message_items")
        duplicates += int(identifier in seen)
        seen.add(identifier)
        required, unsupported = requirements(items)
        locations: list[dict[str, Any]] = []

        for index, item in enumerate(items):
            _locate(item, str(index), locations)
        inspected.append(
            {
                "message_index": ordinal,
                "item_locations": locations,
                "required_media_keys": sorted(required),
                "receiver_state_without_media": "unsupported"
                if unsupported
                else "incomplete"
                if required
                else "pending",
                "identity_fields_present": all(
                    isinstance(message.get(k), str) and bool(message[k].strip())
                    for k in ("from_user_id", "to_user_id")
                ),
                "group_marker_present": bool(message.get("room_id") or message.get("chat_room_id")),
            }
        )
    return {
        "message_count": len(inspected),
        "messages": inspected,
        "duplicate_message_ids": duplicates,
        "state": "conflict"
        if duplicates
        else "unsupported"
        if any(m["receiver_state_without_media"] == "unsupported" for m in inspected)
        else "incomplete"
        if any(m["required_media_keys"] for m in inspected)
        else "inspected",
        "expected_source_count": None,
        "next_actions": ["compare_actual_adapter_and_original_source"]
        + (
            ["supply_media_in_existing_explicit_recovery_flow"]
            if any(m["required_media_keys"] for m in inspected)
            else []
        )
        + (
            ["supply_supported_original_in_new_batch"]
            if any(m["receiver_state_without_media"] == "unsupported" for m in inspected)
            else []
        ),
    }


def inspect(source: bytes, input_kind: str) -> dict[str, Any]:
    """Only counts and fixed locators leave this function; never guess merged-record fields.

    iLink mode reuses the current receiver's media/unsupported classification, without
    checking owner binding or considering descriptors to be downloaded media. Nested
    references are references, not proof of a fully expanded merged-forward archive.
    """
    if input_kind not in {"ilink-poll", "normalized-chat"}:
        raise InvalidInput("payload_input_kind")
    if not isinstance(source, bytes) or not 0 < len(source) <= MAX_BYTES:
        raise InvalidInput("payload_size")
    try:
        value = json.loads(
            source.decode("utf-8"), object_pairs_hook=reject_duplicate, parse_constant=_constant
        )
        if input_kind == "ilink-poll":
            view = _poll(value)
        else:
            candidate = extract_chat(source)
            units = candidate["segments"]
            view = {
                "state": "inspected" if candidate["complete"] else "incomplete",
                "message_count": len(units),
                "expected_source_count": value["expected_count"],
                "messages": [
                    {
                        "message_index": u["locator"]["message_index"],
                        "text_bytes": len(u["text"].encode()),
                        "unknown_source_fields": sum(
                            u["locator"][k] is None
                            for k in ("sender", "sent_at", "source_message_id")
                        ),
                    }
                    for u in units
                ],
                "next_actions": ["compare_actual_adapter_and_original_source"]
                + ([] if candidate["complete"] else ["supply_missing_original_messages"]),
            }
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidInput("payload_json_or_shape_invalid") from None
    return {
        "schema": "fiber-payload-preflight/v1",
        "input_kind": input_kind,
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "source_bytes": len(source),
        **view,
        "platform_completeness": "NOT_VERIFIED",
        "authorization": "NOT_VERIFIED",
        "publication_ready": False,
        "knowledge_verified": False,
    }
