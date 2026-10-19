"""Versioned long-video inputs, finite resource protection and timeline validation."""

from typing import Annotated, Any, Literal

from pydantic import Field

from .config import InvalidInput
from .media_glossary_contract import validate as glossary
from .media_subtitles import gaps
from .payloads import Identifier, Payload

MAX_DURATION_MS = 7_200_000
MAX_SOURCE_BYTES = 16 * 1024 * 1024


class Settings(Payload):
    image: str | None = Field(default=None, pattern=r"^sha256:[a-f0-9]{64}$")
    link_image: str | None = Field(default=None, pattern=r"^sha256:[a-f0-9]{64}$")
    timeout_seconds: Annotated[int, Field(strict=True, ge=30, le=900)] = 120
    frames: bool = False
    asr_resources: dict[str, Any] | None = None


class Create(Payload):
    client_id: Identifier
    import_id: Identifier
    duration_ms: Annotated[int, Field(strict=True, ge=1, le=MAX_DURATION_MS)]
    subtitle_kind: Literal["srt", "vtt"] = "srt"
    subtitle: str = Field(default="", max_length=1024 * 1024)
    frames: bool = False
    glossary: dict[str, Any] | None = None
    glossary_confirmed: bool = False


def segments(duration: int) -> list[dict[str, int]]:
    if type(duration) is not int or not 1 <= duration <= MAX_DURATION_MS:
        raise InvalidInput("video_duration_capacity")
    return [
        {"start_ms": start, "end_ms": min(duration, start + 60000)}
        for start in range(0, duration, 60000)
    ]


def verify(value: Any, spec: dict[str, Any]) -> dict[str, Any]:
    from .media_subtitles import normalized, parse

    if not isinstance(value, dict) or set(value) != {
        "schema",
        "source_sha256",
        "start_ms",
        "end_ms",
        "cues",
        "frames",
        "transcript_route",
    }:
        raise InvalidInput("video_segment_result_invalid")
    if value["schema"] != "fiber-video-segment/v1" or any(
        value[k] != spec[k] for k in ("source_sha256", "start_ms", "end_ms")
    ):
        raise InvalidInput("video_segment_source_changed")
    duration = spec["end_ms"] - spec["start_ms"]
    cues = value["cues"]
    if not isinstance(cues, list) or len(cues) > 2000:
        raise InvalidInput("video_segment_cues_invalid")
    if cues:
        # Host validation uses the existing literal parser rather than trusting tool claims.
        if parse(normalized(cues), "srt", duration) != cues:
            raise InvalidInput("video_segment_cues_invalid")
    route = "supplied_subtitle" if spec["subtitle"] else "local_qwen3_asr"
    if value["transcript_route"] != route:
        raise InvalidInput("video_transcript_route_changed")
    if spec["subtitle"] and cues != spec["cues"]:
        raise InvalidInput("video_subtitle_changed")
    expected = list(range(0, duration, 10000)) if spec["frames"] else []
    frames = value["frames"]
    if not isinstance(frames, list) or len(frames) != len(expected):
        raise InvalidInput("video_segment_frames_invalid")
    import base64

    from .objects import digest

    for i, (frame, timestamp) in enumerate(zip(frames, expected, strict=True)):
        if not isinstance(frame, dict) or set(frame) != {"name", "timestamp_ms", "sha256", "data"}:
            raise InvalidInput("video_segment_frames_invalid")
        raw = base64.b64decode(frame["data"], validate=True)
        if (
            frame["name"] != f"frame-{i:03}.jpg"
            or frame["timestamp_ms"] != timestamp
            or not 0 < len(raw) <= 65536
            or not raw.startswith(b"\xff\xd8")
            or not raw.endswith(b"\xff\xd9")
            or digest(raw) != frame["sha256"]
        ):
            raise InvalidInput("video_segment_frame_changed")
    return {**value, "uncovered_ranges": gaps(cues, duration)}


def confirmed_glossary(value: dict[str, Any] | None, confirmed: bool) -> dict[str, Any]:
    if not confirmed or value is None:
        raise InvalidInput("video_glossary_confirmation_required")
    return glossary(value)
