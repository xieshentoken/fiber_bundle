"""Validated input and on-disk receipt manifests, with explicit missing bytes."""

import json
import re
from dataclasses import dataclass
from typing import Any

from fiber_bundle.config import InvalidInput
from fiber_bundle.objects import digest


def text(value: object, *, limit: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise InvalidInput("invalid text field")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise InvalidInput("control characters are not allowed")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InvalidInput("invalid Unicode text") from exc
    return value


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class AssetInput:
    data: bytes | None
    original_name: str
    media_type: str
    role: str = "attachment"
    expected_sha256: str | None = None
    expected_size: int | None = None

    def descriptor(self) -> dict[str, Any]:
        name = text(self.original_name)
        if name in {".", ".."} or any(c in name for c in "/\\"):
            raise InvalidInput("original name must not contain path components")
        media_type = text(self.media_type, limit=128)
        if not re.fullmatch(r"[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+", media_type):
            raise InvalidInput("invalid media type")
        if not isinstance(self.role, str) or self.role not in {"body", "attachment", "event"}:
            raise InvalidInput("invalid asset role")
        if self.data is not None and not isinstance(self.data, bytes):
            raise InvalidInput("asset data must be bytes or explicitly missing")
        sha = digest(self.data) if self.data is not None else self.expected_sha256
        size = len(self.data) if self.data is not None else self.expected_size
        if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{64}", sha) is None:
            raise InvalidInput("a missing asset requires an expected SHA-256")
        if type(size) is not int or size < 0:
            raise InvalidInput("asset byte count is invalid")
        if self.expected_sha256 is not None and self.expected_sha256 != sha:
            raise InvalidInput("asset differs from expected SHA-256")
        if self.expected_size is not None and (
            type(self.expected_size) is not int or self.expected_size != size
        ):
            raise InvalidInput("asset differs from expected byte count")
        return {
            "sha256": sha,
            "byte_count": size,
            "role": self.role,
            "original_name": name,
            "media_type": media_type,
        }


def manifest(
    *,
    vault: str,
    source_key: str,
    session_scope: str,
    session_id: str,
    retention: str,
    raw_event: bytes,
    items: tuple[AssetInput, ...],
) -> tuple[str, tuple[AssetInput, ...]]:
    for field in (vault, source_key, session_scope, session_id):
        text(field)
    if not isinstance(retention, str) or retention not in {"session", "knowledge"}:
        raise InvalidInput("invalid retention")
    if not isinstance(raw_event, bytes):
        raise InvalidInput("raw event must be non-secret bytes")
    if not isinstance(items, tuple) or not items:
        raise InvalidInput("provide at least one source asset")
    if any(not isinstance(item, AssetInput) or item.role == "event" for item in items):
        raise InvalidInput("invalid source assets")
    all_items = (AssetInput(raw_event, "event.bin", "application/octet-stream", "event"), *items)
    value = {
        "vault": vault,
        "source_key": source_key,
        "session_scope": session_scope,
        "session_id": session_id,
        "retention": retention,
        "assets": [item.descriptor() for item in all_items],
    }
    return canonical(value), all_items


def parse_manifest(value: str) -> dict[str, Any]:
    """Validate persisted data too; never trust arbitrary database paths/types."""
    try:
        data = json.loads(value)
        if not isinstance(data, dict) or set(data) != {
            "vault",
            "source_key",
            "session_scope",
            "session_id",
            "retention",
            "assets",
        }:
            raise InvalidInput("receipt manifest fields are invalid")
        for key in ("vault", "source_key", "session_scope", "session_id"):
            text(data[key])
        if data["retention"] not in {"session", "knowledge"}:
            raise InvalidInput("receipt retention is invalid")
        assets = data["assets"]
        if not isinstance(assets, list) or len(assets) < 2:
            raise InvalidInput("receipt assets are invalid")
        for i, item in enumerate(assets):
            if not isinstance(item, dict) or set(item) != {
                "sha256",
                "byte_count",
                "role",
                "original_name",
                "media_type",
            }:
                raise InvalidInput("receipt asset fields are invalid")
            checked = AssetInput(
                None,
                item["original_name"],
                item["media_type"],
                item["role"],
                item["sha256"],
                item["byte_count"],
            ).descriptor()
            if checked != item or (item["role"] == "event") != (i == 0):
                raise InvalidInput("receipt asset role is invalid")
        return data
    except (ValueError, TypeError, KeyError) as exc:
        raise InvalidInput("receipt manifest is invalid") from exc
