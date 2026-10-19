"""Minimal source metadata and request-bound preview validation, never media approval."""

import hashlib
import math
from pathlib import Path
from typing import Any

from .config import InvalidInput
from .media_files import canonical, decode
from .media_link_contract import source_url
from .media_link_failure import AcquisitionBlocked
from .media_link_parts import parts, reported_size
from .media_processing_files import read


def checked_info(info: object, url: str, *, mode: str = "audio_only") -> dict[str, Any]:
    if (
        not isinstance(info, dict)
        or info.get("_type", "video") != "video"
        or info.get("is_live")
        or info.get("live_status") in ("is_live", "is_upcoming", "post_live")
        or info.get("has_drm")
    ):
        raise AcquisitionBlocked("source_unsupported")
    if info.get("availability") in ("private", "needs_auth", "premium_only", "subscriber_only"):
        raise AcquisitionBlocked("access_restricted")
    parts(info, mode)
    expected = url.split("v=")[-1] if "youtube.com" in url else url.rsplit("/", 1)[-1]
    if info.get("id") != expected:
        raise AcquisitionBlocked("source_id_mismatch")
    return info


def _number(value: object, maximum: int) -> int | float | None:
    return (
        value
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and 0 < value <= maximum
            and math.isfinite(value)
        )
        else None
    )


def observation(
    spec: dict[str, Any], seconds: object, size: object, basis: str | None
) -> dict[str, Any]:
    duration = _number(seconds, 604800)
    amount = _number(size, 2**53)
    milliseconds = round(duration * 1000) if duration is not None else None
    size_bytes = math.ceil(amount) if amount is not None else None
    reason = None
    if milliseconds is None or milliseconds < 1:
        reason = "duration_unverified"
    elif milliseconds > 60000:
        reason = "duration_over_limit"
    elif size_bytes is None:
        reason = "size_unverified"
    elif size_bytes > 8 * 1024 * 1024:
        reason = "size_over_limit"
    return {
        "schema": "fiber-media-link-preflight/v1",
        "request_sha256": hashlib.sha256(canonical(spec)).hexdigest(),
        "observed_duration_ms": milliseconds,
        "reported_bytes": size_bytes,
        "size_basis": basis if amount is not None else None,
        "eligible_by_metadata": reason is None,
        "reason": reason,
        "source_coverage_verified": False,
        "login_verified": False,
        "publication_ready": False,
        "cookies_exported": False,
    }


def inspect(info: object, spec: dict[str, Any]) -> dict[str, Any]:
    if (
        set(spec) != {"kind", "url", "mode"}
        or spec["kind"] != "metadata_preflight"
        or spec["url"] != source_url(spec["url"])
        or spec["mode"] not in ("audio_only", "full_evidence")
    ):
        raise AcquisitionBlocked("input_invalid")
    selected = checked_info(info, spec["url"], mode=spec["mode"])
    size, basis = reported_size(selected, spec["mode"])
    return observation(
        spec,
        selected.get("duration"),
        size,
        basis,
    )


def verify(root: Path, spec: dict[str, Any]) -> dict[str, Any]:
    if read(root / "input", "request.json", 4096) != canonical(spec):
        raise InvalidInput("media_link_preflight_request_changed")
    if {p.name for p in (root / "output").iterdir()} != {"result.json"}:
        raise InvalidInput("media_link_preflight_output_invalid")
    value = decode(read(root / "output", "result.json", 2048))
    duration, size, basis = (
        value.get(k) for k in ("observed_duration_ms", "reported_bytes", "size_basis")
    )
    if (
        (duration is not None and (type(duration) is not int or not 0 <= duration <= 604800000))
        or (size is not None and (type(size) is not int or not 1 <= size <= 2**53))
        or (basis not in (None, "reported", "estimated"))
        or ((size is None) != (basis is None))
    ):
        raise InvalidInput("media_link_preflight_scalar_invalid")
    expected = observation(spec, duration / 1000 if duration else None, size, basis)
    # A positive sub-millisecond metadata duration rounds to zero and remains ineligible.
    if duration == 0:
        expected["observed_duration_ms"] = 0
    if canonical(value) != canonical(expected):
        raise InvalidInput("media_link_preflight_binding_invalid")
    return value
