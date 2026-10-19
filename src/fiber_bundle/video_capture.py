"""Offline long-video segment tool, reusing local ASR and frame extraction."""

import json
import subprocess
from pathlib import Path

from .config import InvalidInput
from .inputs import canonical
from .media_processing_capture import asr, extract_frames
from .media_processing_files import read, write
from .media_transcript import convert
from .objects import digest
from .video_contract import MAX_DURATION_MS, MAX_SOURCE_BYTES


def capture(root: Path, temporary: Path = Path("/tmp")) -> None:
    spec = json.loads(read(root / "input", "request.json", 2 * 1024 * 1024))
    source = root / "input/source"
    raw = read(root / "input", "source", MAX_SOURCE_BYTES)
    if digest(raw) != spec["source_sha256"]:
        raise InvalidInput("video_source_changed")
    # Actual duration is measured independently. User declaration is not evidence.
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-i",
            str(source),
            "-show_entries",
            "format=duration",
            "-of",
            "json",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=20,
    )
    if len(result.stdout) > 1024:
        raise InvalidInput("video_probe_invalid")
    duration = round(float(json.loads(result.stdout)["format"]["duration"]) * 1000)
    if not 1 <= duration <= MAX_DURATION_MS or abs(duration - spec["duration_ms"]) > 100:
        raise InvalidInput("video_source_duration_mismatch")
    start, end = spec["start_ms"], spec["end_ms"]
    if not 0 <= start < end <= spec["duration_ms"] or end - start > 60000:
        raise InvalidInput("video_segment_bounds")
    clip = temporary / ("segment." + spec["format"])
    args = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-xerror",
        "-threads",
        "1",
        "-protocol_whitelist",
        "file",
        "-ss",
        str(start / 1000),
        "-i",
        str(source),
        "-t",
        str((end - start) / 1000),
    ]
    args += (
        ["-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"]
        if spec["format"] == "wav"
        else [
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-vf",
            "scale=640:-2",
            "-c:v",
            "mpeg4",
            "-q:v",
            "8",
            "-c:a",
            "aac",
            "-threads",
            "1",
        ]
    )
    subprocess.run(
        args + [str(clip)],
        check=True,
        timeout=90,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if spec["subtitle"]:
        cues = spec["cues"]
        route = "supplied_subtitle"
    else:
        resources = spec["asr_resources"]
        if not resources:
            raise InvalidInput("video_transcription_capability_missing")
        local = asr(clip, {"policy": resources, "duration_ms": end - start})
        transcript = convert(local, spec["glossary"], end - start)
        cues, route = transcript["cues"], "local_qwen3_asr"
        write(root / "output", "raw.json", canonical({"chunks": local}).encode())
    frames = (
        extract_frames(clip, end - start, root / "output", temporary=temporary)
        if spec["frames"]
        else []
    )
    value = {
        "schema": "fiber-video-segment/v1",
        "source_sha256": spec["source_sha256"],
        "start_ms": start,
        "end_ms": end,
        "cues": cues,
        "frames": frames,
        "transcript_route": route,
    }
    write(root / "output", "result.json", canonical(value).encode())


def main() -> None:
    try:
        capture(Path("/task"))
    except InvalidInput as error:
        # Only finite application codes; never decoder or source text.
        known = {
            "video_source_changed",
            "video_source_duration_mismatch",
            "video_segment_bounds",
            "video_transcription_capability_missing",
            "video_probe_invalid",
        }
        print(str(error) if str(error) in known else "video_capture_invalid")
        raise SystemExit(1) from None
    except Exception:
        print("video_capture_failed")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
