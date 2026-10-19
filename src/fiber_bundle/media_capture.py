"""Container-only fixed ffprobe/ffmpeg route and host-side candidate verification."""

import subprocess
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .config import InvalidInput
from .media_candidate import candidate, metadata, request
from .media_contract import MAX_MEDIA_BYTES
from .media_files import MAX_RESULT_BYTES, canonical, decode, read, write
from .media_subtitles import MAX_SUBTITLE_BYTES, normalized


def probe(path: Path, spec: dict[str, Any]) -> dict[str, Any]:
    # No URLs, playlists, tags, attached pictures, data or subtitle streams are supported.
    args = [
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        spec["format"] if (spec["format"] == "wav") else "mov",
        "-f",
        "wav" if spec["format"] == "wav" else "mov",
        "-probesize",
        "8388608",
        "-analyzeduration",
        "5000000",
        "-i",
        str(path),
    ]
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            *args,
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,sample_rate,channels,width,height",
            "-of",
            "json",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=20,
        check=True,
    )
    if len(completed.stdout) > 8192:
        raise InvalidInput("media_probe_size")
    import json

    try:
        raw = json.loads(completed.stdout)
        duration = Decimal(raw["format"]["duration"])
        if not duration.is_finite():
            raise InvalidInput("media_duration")
        streams = []
        for stream in raw["streams"]:
            row = {"kind": stream["codec_type"], "codec": stream["codec_name"]}
            keys = ("sample_rate", "channels") if row["kind"] == "audio" else ("width", "height")
            row.update({key: int(stream[key]) for key in keys})
            streams.append(row)
        value = metadata(
            {
                "duration_ms": int((duration * 1000).quantize(Decimal(1), rounding=ROUND_HALF_UP)),
                "streams": streams,
            },
            spec["format"],
        )
    except (ValueError, KeyError, TypeError, InvalidOperation, OverflowError) as exc:
        raise InvalidInput("media_probe_invalid") from exc
    maps = ["-map", "0:a?"]
    if spec["evidence_mode"] == "full_evidence":
        maps += ["-map", "0:v?"]
    if not any(s["kind"] == "audio" for s in streams) and spec["evidence_mode"] == "audio_only":
        raise InvalidInput("media_audio_stream_missing")
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-threads",
            "1",
            *args,
            *maps,
            "-threads",
            "1",
            "-f",
            "null",
            "-",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=60,
        check=True,
    )
    return value


def verify_snapshot(
    inputs: Path, source: bytes, subtitle: bytes | None, spec: dict[str, Any]
) -> None:
    names = {"source", "request.json"} | ({"subtitle"} if subtitle is not None else set())
    if {p.name for p in inputs.iterdir()} != names or (
        read(inputs, "source", MAX_MEDIA_BYTES) != source
        or read(inputs, "request.json", 4096) != canonical(spec)
        or (subtitle is not None and read(inputs, "subtitle", MAX_SUBTITLE_BYTES) != subtitle)
    ):
        raise InvalidInput("media_snapshot_changed")


def capture(inputs: Path, outputs: Path) -> None:
    spec = decode(read(inputs, "request.json", 4096))
    request(spec)
    expected = {"request.json", "source"} | ({"subtitle"} if spec["subtitle_kind"] else set())
    if {p.name for p in inputs.iterdir()} != expected or any(outputs.iterdir()):
        raise InvalidInput("media_capture_scope")
    source = read(inputs, "source", MAX_MEDIA_BYTES)
    subtitle = read(inputs, "subtitle", MAX_SUBTITLE_BYTES) if spec["subtitle_kind"] else None
    value = candidate(source, subtitle, spec, probe(inputs / "source", spec))
    raw = canonical(value)
    if len(raw) > MAX_RESULT_BYTES:
        raise InvalidInput("media_result_limit")
    if value["subtitle"]["cues"]:
        write(outputs, "normalized.srt", normalized(value["subtitle"]["cues"]))
    write(outputs, "result.json", raw)


def verify(
    outputs: Path, source: bytes, subtitle: bytes | None, spec: dict[str, Any]
) -> dict[str, Any]:
    value = decode(read(outputs, "result.json", MAX_RESULT_BYTES))
    if value != candidate(source, subtitle, spec, value.get("probe")):
        raise InvalidInput("media_candidate_binding")
    names = {"result.json"}
    if value["subtitle"]["cues"]:
        names.add("normalized.srt")
        if read(outputs, "normalized.srt", MAX_SUBTITLE_BYTES) != normalized(
            value["subtitle"]["cues"]
        ):
            raise InvalidInput("media_normalized_binding")
    if {p.name for p in outputs.iterdir()} != names:
        raise InvalidInput("media_output_scope")
    return value
