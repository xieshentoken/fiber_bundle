"""Direct-file subtitle candidates; source bytes and literal captions are independently bound."""

import hashlib
from typing import Any

from .config import InvalidInput
from .media_contract import MAX_DURATION_MS, MAX_MEDIA_BYTES
from .media_subtitles import gaps, normalized, parse

MIMES = {"audio/wav": "wav", "audio/x-wav": "wav", "video/mp4": "mp4"}
SUBTITLE_MIMES = {"application/x-subrip": "srt", "text/vtt": "vtt"}


def metadata(value: object, format: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"duration_ms", "streams"}:
        raise InvalidInput("media_probe_contract")
    duration = value["duration_ms"]
    streams = value["streams"]
    if type(duration) is not int or not 1 <= duration <= MAX_DURATION_MS:
        raise InvalidInput("media_duration")
    if not isinstance(streams, list) or not 1 <= len(streams) <= 2:
        raise InvalidInput("media_streams")
    seen = set()
    for row in streams:
        if not isinstance(row, dict) or not isinstance(row.get("kind"), str) or row["kind"] in seen:
            raise InvalidInput("media_streams")
        kind = row.get("kind")
        if kind == "audio":
            if set(row) != {"kind", "codec", "sample_rate", "channels"} or (
                row["codec"] != ("pcm_s16le" if format == "wav" else "aac")
                or type(row["sample_rate"]) is not int
                or not 8000 <= row["sample_rate"] <= 48000
                or type(row["channels"]) is not int
                or not 1 <= row["channels"] <= 2
            ):
                raise InvalidInput("media_audio_matrix")
        elif kind == "video" and format == "mp4":
            if set(row) != {"kind", "codec", "width", "height"} or (
                row["codec"] != "mpeg4"
                or type(row["width"]) is not int
                or type(row["height"]) is not int
                or not 1 <= row["width"] <= 1920
                or not 1 <= row["height"] <= 1080
            ):
                raise InvalidInput("media_video_matrix")
        else:
            raise InvalidInput("media_stream_kind")
        seen.add(kind)
    if (format == "wav" and seen != {"audio"}) or (format == "mp4" and "video" not in seen):
        raise InvalidInput("media_format_matrix")
    return value


def request(value: dict[str, Any]) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != {"format", "evidence_mode", "declared_duration_ms", "subtitle_kind"}
        or (
            not isinstance(value["format"], str)
            or value["format"] not in {"wav", "mp4"}
            or not isinstance(value["evidence_mode"], str)
            or value["evidence_mode"] not in {"audio_only", "full_evidence"}
            or (value["format"] == "wav" and value["evidence_mode"] != "audio_only")
            or type(value["declared_duration_ms"]) is not int
            or not 1 <= value["declared_duration_ms"] <= MAX_DURATION_MS
            or (value["subtitle_kind"] is not None and not isinstance(value["subtitle_kind"], str))
            or value["subtitle_kind"] not in {None, "srt", "vtt"}
        )
    ):
        raise InvalidInput("media_request")


def candidate(
    source: bytes, subtitle: bytes | None, spec: dict[str, Any], probe: object
) -> dict[str, Any]:
    request(spec)
    if not 0 < len(source) <= MAX_MEDIA_BYTES or (subtitle is None) != (
        spec["subtitle_kind"] is None
    ):
        raise InvalidInput("media_source_binding")
    probe = metadata(probe, spec["format"])
    duration = probe["duration_ms"]
    cues = parse(subtitle, spec["subtitle_kind"], duration) if subtitle is not None else []
    missing = gaps(cues, duration)
    reasons = []
    if duration != spec["declared_duration_ms"]:
        reasons.append("source_duration_mismatch")
    if subtitle is None:
        reasons.append("subtitle_missing")
    elif missing:
        reasons.append("subtitle_coverage_gaps")
    if spec["evidence_mode"] == "full_evidence":
        reasons.append("visual_not_reviewed")
    return {
        "schema": "fiber-media-candidate/v1",
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "source_bytes": len(source),
        "request": spec,
        "probe": probe,
        "subtitle": {
            "provenance": "user_supplied_separate_asset" if subtitle is not None else "none",
            "raw_sha256": hashlib.sha256(subtitle).hexdigest() if subtitle is not None else None,
            "normalized_sha256": hashlib.sha256(normalized(cues)).hexdigest() if cues else None,
            "cues": cues,
            "uncovered_ranges": missing,
            "corrections": [],
            "semantic_review_verified": False,
        },
        "state": "blocked"
        if "source_duration_mismatch" in reasons
        else ("pending" if reasons else "candidate"),
        "reasons": reasons,
        "source_bytes_verified": True,
        "disclosure": "音频优先整理，未核验画面内容"
        if spec["evidence_mode"] == "audio_only"
        else None,
        "visual_review_verified": False,
        "publication_ready": False,
        "knowledge_verified": False,
        "cleanup_allowed": False,
        "original_retention": "keep_original",
        "model_calls": 0,
    }
