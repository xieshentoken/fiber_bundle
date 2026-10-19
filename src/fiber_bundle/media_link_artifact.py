"""Versioned capture binding: platform duration and converted-file duration stay distinct."""

import hashlib
from typing import Any

from .config import InvalidInput
from .media_files import canonical


def duration(
    value: dict[str, Any], data: bytes, spec: dict[str, Any], *, browser: bool = False
) -> int:
    version = value.get("schema")
    measured = value.get("converted_duration_ms")
    if version == "fiber-media-link-capture/v2":
        if type(measured) is not int or not 1 <= measured <= 60000:
            raise InvalidInput("media_link_converted_duration")
    elif version == "fiber-media-link-capture/v1":
        measured = spec["declared_duration_ms"]
    else:
        raise InvalidInput("media_link_capture_version")
    state = value.get("access_state")
    allowed = ("public_acquired", "browser_copy_attested") if browser else ("public_acquired",)
    expected = {
        "schema": version,
        **spec,
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "source_bytes": len(data),
        "media_type": "audio/wav" if spec["mode"] == "audio_only" else "video/mp4",
        "access_state": state,
        "source_coverage_verified": False,
        "cookies_exported": False,
    }
    if version == "fiber-media-link-capture/v2":
        expected["converted_duration_ms"] = measured
    if state not in allowed or canonical(value) != canonical(expected):
        raise InvalidInput("media_link_result_binding")
    # This is a claim about converted bytes, re-probed by the independent media worker.
    # Neither duration nor a successful download certifies complete platform coverage.
    return int(measured)
