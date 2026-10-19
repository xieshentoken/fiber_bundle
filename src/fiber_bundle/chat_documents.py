"""Explicit normalized chat transport. Unknown authors/times remain null; partial stays partial."""

import json
from typing import Any

from .document_capture import reject_duplicate
from .document_contract import DocumentError
from .material_contract import result, segment


def extract(source: bytes) -> dict[str, Any]:
    try:
        value = json.loads(source, object_pairs_hook=reject_duplicate)
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "expected_count", "messages"}
            or value["schema"] != "fiber-chat-source/v1"
        ):
            raise DocumentError("chat_source_shape")
        messages = value["messages"]
        expected = value["expected_count"]
        if (
            not isinstance(messages, list)
            or not 1 <= len(messages) <= 100
            or (
                expected is not None
                and (type(expected) is not int or not len(messages) <= expected <= 100)
            )
        ):
            raise DocumentError("chat_source_count")
        units = []
        for i, message in enumerate(messages, 1):
            if (
                not isinstance(message, dict)
                or set(message) != {"text", "sender", "sent_at", "source_message_id"}
                or not isinstance(message["text"], str)
                or not message["text"].strip()
            ):
                raise DocumentError("chat_message_shape")
            for key in ("sender", "sent_at", "source_message_id"):
                if message[key] is not None and (
                    not isinstance(message[key], str) or len(message[key]) > 400
                ):
                    raise DocumentError("chat_source_field")
            units.append(
                segment(
                    message["text"],
                    {
                        "message_index": i,
                        **{k: message[k] for k in ("sender", "sent_at", "source_message_id")},
                    },
                    ["source_fields_unknown"]
                    if any(message[k] is None for k in ("sender", "sent_at", "source_message_id"))
                    else [],
                )
            )
        return result(
            source,
            "chat",
            units,
            runtime={"parser": "fiber-chat-source/v1", "platform_completeness": "not_verified"},
            complete=expected == len(messages),
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, DocumentError):
            raise
        raise DocumentError("chat_source_invalid") from exc
