"""Explicit, model-free note recovery from already audited visual source material."""

import html
import json
import re
import sqlite3
import wave
from pathlib import Path
from typing import Any

from .config import InvalidInput, contained
from .objects import digest

MODE = "visual_evidence"
CAPTURED_MODE = "captured_evidence"
MODES = {MODE, CAPTURED_MODE}


def require_cached(
    db: sqlite3.Connection,
    frozen: dict[str, Any],
    job: Path | None = None,
    *,
    mode: str | None = None,
) -> None:
    """Missing or changed evidence must never trigger another inference as a fallback."""
    required = {
        "initialize",
        "terms",
        "public_metadata",
        "media",
        "subtitle_attempt",
        "transcript",
        "frames",
        "crops",
        "timeline",
    }
    runs = frozen.get("vision_runs", {})
    selected = mode or frozen.get("note_authoring", MODE)
    routes = {"visual_primary"}
    if selected == CAPTURED_MODE:
        routes.update({"platform_subtitle", "local_qwen3_asr", "transcription_api"})
    if (
        selected not in MODES
        or frozen.get("transcript_route") not in routes
        or not required.issubset(frozen.get("completed", []))
        or not runs
    ):
        raise InvalidInput("video_skill_visual_audit_failed")
    for rid in runs.values():
        row = db.execute("SELECT state FROM execution_runs WHERE id=?", (rid,)).fetchone()
        if row is None or row[0] != "succeeded":
            raise InvalidInput("video_skill_visual_audit_failed")
    if job is None:
        return
    try:
        if frozen["transcript_route"] != "visual_primary":
            require_transcript(frozen, job)
        frames = json.loads((job / "frames/frame_manifest.json").read_bytes())["frames"]
        rows = [
            json.loads(line)
            for line in (job / "visual/visual_results.jsonl").read_text().splitlines()
            if line
        ]
        by_id = {row["frame_id"]: row for row in rows}
        if len(by_id) != len(rows) or len(rows) != len(frames) or set(by_id) != set(runs):
            raise ValueError()
        for frame in frames:
            fid = frame["frame_id"]
            row = by_id[fid]
            if (
                row != json.loads(contained(job, "visual/" + fid + ".json").read_bytes())
                or row["run_id"] != runs[fid]
                or row["visual_verification"]["input_sha256"] != frame["sha256"]
                or digest(contained(job, frame["selected_path"]).read_bytes()) != frame["sha256"]
            ):
                raise ValueError()
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise InvalidInput("video_skill_visual_audit_failed") from error


def require_transcript(frozen: dict[str, Any], job: Path) -> None:
    from .media_subtitles import parse
    from .query_routing_files import read

    route = frozen["transcript_route"]
    relative = "transcript/" + (
        "subtitle_normalized.srt" if route == "platform_subtitle" else "asr_normalized.srt"
    )
    raw = read(contained(job, relative), 1024 * 1024)
    coverage = json.loads(read(contained(job, "evidence/coverage.json"), 1024 * 1024))
    duration = max(
        float(coverage["source_declared_duration_seconds"]),
        float(coverage["source_acquired_duration_seconds"]),
    )
    if route == "transcription_api":
        from .video_skill import media_digest

        # Decoded AAC can retain a partial codec frame (e.g. 32 ms). Use its
        # measured duration only when the saved source and audio hashes match.
        audio = contained(job, "transcript/asr_audio.wav")
        diagnostic = json.loads(read(contained(job, "transcript/asr_diagnostic.json"), 2**20))
        if diagnostic["source_media_sha256"] != frozen["source_media_sha256"] or diagnostic[
            "audio_sha256"
        ] != media_digest(audio):
            raise InvalidInput("video_skill_visual_audit_failed")
        with wave.open(str(audio)) as stream:
            audio_duration = stream.getnframes() / stream.getframerate()
        if audio_duration > duration + 0.25:
            raise InvalidInput("video_skill_visual_audit_failed")
        duration = max(duration, audio_duration)
    cues = parse(raw, "srt", round(duration * 1000), long_video=True)
    segments = [
        json.loads(line)
        for line in read(contained(job, "evidence/segments.jsonl"), 4 * 1024 * 1024).splitlines()
        if line
    ]
    text = "".join("".join(s.get("transcript", "").split()) for s in segments)
    if (
        not cues
        or coverage["transcript_file"] != relative
        or coverage["transcript_coverage_ratio"] != 1
        or coverage["covered_transcript_cues"] != len(cues)
        or coverage["transcript_cues"] != len(cues)
        or any("".join(cue["text"].split()) not in text for cue in cues)
        or frozen.get("evidence_transcript_sha256", digest(raw)) != digest(raw)
    ):
        raise InvalidInput("video_skill_visual_audit_failed")
    frozen["evidence_transcript_sha256"] = digest(raw)


def literal(value: str) -> str:
    # Source text is displayed literally; it cannot inject HTML or image references.
    return re.sub(r"([\\`*_{}\[\]()!#|])", r"\\\1", html.escape(value, quote=False))


def segment_note(evidence: dict[str, Any]) -> str:
    parts: list[str] = []
    transcript = evidence.get("transcript", "")
    if not isinstance(transcript, str):
        raise InvalidInput("video_skill_visual_content_missing")
    useful = bool(transcript.strip())
    if useful:
        parts += ["转写原文（未人工校对）：", "", literal(transcript), ""]
    for frame in evidence["visual_evidence"]:
        analysis = frame["analysis"]
        for key in ("ocr_text", "claims"):
            items = analysis.get(key)
            if not isinstance(items, list) or any(not isinstance(x, str) for x in items):
                raise InvalidInput("video_skill_visual_content_missing")
            useful |= any(x.strip() for x in items)
        parts += ["", f"画面时间点：{frame['timestamp']:.3f} 秒", ""]
        if analysis.get("summary"):
            if not isinstance(analysis["summary"], str):
                raise InvalidInput("video_skill_visual_content_missing")
            parts += ["画面分析摘要：" + literal(analysis["summary"]), ""]
        if analysis["ocr_text"]:
            parts += ["画面文字（OCR，未人工校对）：", ""]
            for text in analysis["ocr_text"]:
                parts += ["> " + literal(line) for line in text.splitlines()]
            parts += [""]
        if analysis["claims"]:
            parts += ["画面分析要点（模型提取，未独立核验）：", ""]
            parts += ["- " + literal(text).replace("\n", " ") for text in analysis["claims"]]
    if not useful:
        raise InvalidInput("video_skill_visual_content_missing")
    return "\n".join(parts)
