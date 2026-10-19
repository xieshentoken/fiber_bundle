"""Strict advisory metadata contract for media; never a task, grant or publication."""

import re
from typing import Any

from .config import InvalidInput

MAX_MANIFEST_BYTES = 64 * 1024
MAX_MEDIA_BYTES = 8 * 1024 * 1024  # Existing Store boundary, not a new import allowance.
MAX_DURATION_MS = 3_600_000
MAX_RANGES = 100


def _fields(value: object, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise InvalidInput("media_fields")
    return value


def _choice(value: object, choices: set[str]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise InvalidInput("media_choice")
    return value


def _integer(value: object, maximum: int, *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise InvalidInput("media_number")
    return value


def _sha(value: object, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise InvalidInput("media_sha256")


def ranges(value: object, duration: int, *, cues: bool = False) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_RANGES:
        raise InvalidInput("media_ranges")
    previous = -1
    for row in value:
        item = _fields(
            row, {"start_ms", "end_ms", "text_sha256"} if cues else {"start_ms", "end_ms"}
        )
        start = _integer(item["start_ms"], duration)
        end = _integer(item["end_ms"], duration, minimum=1)
        if start >= end or start < previous:
            raise InvalidInput("media_timestamp_order_or_bounds")
        previous = start
        if cues:
            _sha(item["text_sha256"])
    return value


def validate(value: object) -> dict[str, Any]:
    """SHA claims are shape-checked, not verified against media, transcript or human records.

    Future receipt integration must bind real bytes and current grants independently.
    Paths, URLs, raw transcript, credentials and arbitrary legacy V3 packages are refused.
    """
    job = _fields(value, {"schema", "evidence_mode", "source", "transcript", "glossary", "visual"})
    if job["schema"] != "fiber-media-preflight-source/v1":
        raise InvalidInput("media_schema")
    mode = _choice(job["evidence_mode"], {"audio_only", "full_evidence"})
    source = _fields(
        job["source"],
        {
            "sha256",
            "byte_count",
            "media_kind",
            "origin",
            "source_state",
            "ownership",
            "local_duration_ms",
            "declared_duration_ms",
            "keep_media",
        },
    )
    _sha(source["sha256"])
    _integer(source["byte_count"], MAX_MEDIA_BYTES, minimum=1)
    kind = _choice(source["media_kind"], {"audio", "video"})
    _choice(source["origin"], {"direct", "link"})
    _choice(source["source_state"], {"complete", "partial", "preview_limited", "unverified"})
    owner = _choice(source["ownership"], {"user_original", "job_owned"})
    if source["origin"] == "direct" and owner != "user_original":
        raise InvalidInput("media_direct_original_ownership")
    if kind == "audio" and mode != "audio_only":
        raise InvalidInput("media_audio_mode")
    duration = _integer(source["local_duration_ms"], MAX_DURATION_MS, minimum=1)
    if source["declared_duration_ms"] is not None:
        _integer(source["declared_duration_ms"], MAX_DURATION_MS, minimum=1)
    if type(source["keep_media"]) is not bool:
        raise InvalidInput("media_keep_flag")
    transcript = _fields(
        job["transcript"],
        {
            "kind",
            "raw_sha256",
            "normalized_sha256",
            "cues",
            "failed_ranges",
        },
    )
    route = _choice(transcript["kind"], {"none", "subtitle", "asr"})
    for name in ("raw_sha256", "normalized_sha256"):
        _sha(transcript[name], nullable=route == "none")
    ranges(transcript["cues"], duration, cues=True)
    ranges(transcript["failed_ranges"], duration)
    if route == "none" and (
        transcript["raw_sha256"] is not None
        or transcript["normalized_sha256"] is not None
        or transcript["cues"]
        or transcript["failed_ranges"]
    ):
        raise InvalidInput("media_absent_transcript")
    glossary = _fields(job["glossary"], {"sha256", "confirmed_sha256", "unresolved_count"})
    _sha(glossary["sha256"], nullable=True)
    _sha(glossary["confirmed_sha256"], nullable=True)
    _integer(glossary["unresolved_count"], MAX_RANGES)
    visual = _fields(job["visual"], {"state", "frame_count", "unresolved_count", "coverage_ranges"})
    state = _choice(visual["state"], {"not_applicable", "missing", "partial", "reviewed"})
    count = _integer(visual["frame_count"], MAX_RANGES)
    _integer(visual["unresolved_count"], MAX_RANGES)
    ranges(visual["coverage_ranges"], duration)
    if mode == "audio_only" and (
        state != "not_applicable"
        or count
        or visual["unresolved_count"]
        or visual["coverage_ranges"]
    ):
        raise InvalidInput("media_audio_only_visual_claim")
    if mode == "full_evidence" and state == "not_applicable":
        raise InvalidInput("media_visual_required")
    return job
