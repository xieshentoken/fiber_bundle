#!/usr/bin/env python3
"""Run terminology-aware local ASR and normalize its timestamped transcript."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from common import (
    Cue,
    read_json,
    register_managed_file,
    render_srt,
    sha256_file,
    sha256_json,
    update_pipeline_state,
    write_json,
)
from normalize_transcript import normalize_transcript_file


AUTHORIZED = {"public", "authenticated", "member_authorized"}
DEFAULT_MODEL = "Qwen/Qwen3-ASR-0.6B"


def qwen_python_candidates(explicit: str | None = None) -> list[Path]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    # Prefer project-scoped configuration. Keep the legacy variable as a
    # compatibility path for existing local installations.
    for name in ("BILI_NOTE_QWEN_PYTHON", "RIMAGINATION_QWEN_PYTHON"):
        if os.environ.get(name):
            candidates.append(Path(os.environ[name]).expanduser())
    cache_value = os.environ.get("BILI_NOTE_QWEN_CACHE") or os.environ.get(
        "RIMAGINATION_NOTE_CACHE"
    )
    cache = (
        Path(cache_value).expanduser()
        if cache_value
        else Path.home() / ".cache" / "bili-note-v2"
    )
    for root in (
        cache / "qwen3-asr-venv",
        # Legacy cache locations remain discoverable so an existing ASR
        # environment does not need to be rebuilt after upgrading the skill.
        Path.home() / ".cache" / "rimagination-notes" / "qwen3-asr-venv",
        Path.home() / ".cache" / "dy-note" / "qwen3-asr-venv",
        Path.home() / ".cache" / "douyin-note" / "qwen3-asr-venv",
    ):
        candidates.extend((root / "bin" / "python", root / "Scripts" / "python.exe"))
    if importlib.util.find_spec("qwen_asr") is not None:
        candidates.append(Path(sys.executable))
    return list(dict.fromkeys(candidates))


def python_has_qwen(executable: Path, timeout: int = 20) -> bool:
    if not executable.is_file():
        return False
    try:
        result = subprocess.run(
            [str(executable), "-c", "import qwen_asr, soundfile, torch"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def find_qwen_python(explicit: str | None = None) -> Path | None:
    for candidate in qwen_python_candidates(explicit):
        if python_has_qwen(candidate):
            return candidate
    return None


def build_asr_context(context: dict[str, Any]) -> str:
    lines: list[str] = []
    topic = str(context.get("topic") or "").strip()
    if topic:
        lines.append(f"Topic: {topic}.")
    prompt = str(context.get("initial_prompt") or "").strip()
    if prompt:
        lines.append(prompt.rstrip(".;") + ".")
    hotwords = [str(value).strip() for value in context.get("hotwords", []) if str(value).strip()]
    if hotwords:
        lines.append("Preserve these exact specialist terms and capitalization: " + ", ".join(hotwords) + ".")
    return " ".join(lines)


def segments_to_cues(payload: dict[str, Any]) -> list[Cue]:
    cues: list[Cue] = []
    for row in payload.get("segments", []):
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        start = float(row.get("start", 0.0))
        end = float(row.get("end", start))
        if end <= start:
            raise ValueError(f"ASR segment has an invalid time range: {row}")
        cues.append(Cue(index=len(cues) + 1, start=start, end=end, text=text))
    if not cues:
        raise ValueError("ASR backend returned no timestamped transcript segments")
    return cues


def resolve_media(job_dir: Path, explicit: Path | None) -> Path:
    if explicit:
        media = explicit.expanduser().resolve()
        if not media.is_file():
            raise FileNotFoundError(f"Media file not found: {media}")
        return media
    candidates = sorted(
        path
        for pattern in ("video.*", "audio.*")
        for path in (job_dir / "source").glob(pattern)
        if path.is_file()
    )
    if len(candidates) != 1:
        raise FileNotFoundError(
            "Expected exactly one source/video.* or source/audio.* file; pass --media explicitly"
        )
    return candidates[0]


def extract_audio(media: Path, audio: Path, force: bool) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to prepare ASR audio")
    if audio.exists() and not force:
        return
    audio.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(media),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(audio),
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extraction failed: {result.stderr[-1600:]}")


def probe_audio_duration(audio: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe is required to audit ASR coverage")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(audio)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe audio duration failed: {result.stderr[-1200:]}")
    payload = json.loads(result.stdout or "{}")
    return float(payload.get("format", {}).get("duration") or 0.0)


def build_coverage_report(
    cues: list[Cue], duration: float, chunk_results: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Record transcript coverage and explicit gaps instead of hiding failures."""
    ranges = sorted(
        (max(0.0, float(cue.start or 0.0)), min(duration, float(cue.end or 0.0)))
        for cue in cues
        if cue.start is not None and cue.end is not None and cue.end > cue.start
    )
    merged: list[list[float]] = []
    for start, end in ranges:
        if not merged or start > merged[-1][1] + 0.05:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    gaps: list[dict[str, float]] = []
    cursor = 0.0
    for start, end in merged:
        if start > cursor + 0.05:
            gaps.append({"start": round(cursor, 3), "end": round(start, 3)})
        cursor = max(cursor, end)
    if duration > cursor + 0.05:
        gaps.append({"start": round(cursor, 3), "end": round(duration, 3)})
    covered = sum(end - start for start, end in merged)
    return {
        "schema_version": "1.0",
        "duration_seconds": round(duration, 3),
        "transcript_segments": len(cues),
        "covered_seconds": round(covered, 3),
        "coverage_ratio": round(covered / duration, 4) if duration else 0.0,
        "gap_ranges": gaps,
        "chunk_results": chunk_results or [],
        "failed_chunk_count": sum(
            1 for item in (chunk_results or []) if item.get("status") != "complete"
        ),
    }


def run_qwen(
    python: Path,
    skill_dir: Path,
    audio: Path,
    output: Path,
    context_file: Path,
    model: str,
    language: str,
    device_map: str,
    dtype: str,
    chunk_seconds: float,
    chunk_overlap_seconds: float,
) -> None:
    command = [
        str(python),
        str(skill_dir / "scripts" / "qwen3_asr_backend.py"),
        "--audio",
        str(audio),
        "--out",
        str(output),
        "--context-file",
        str(context_file),
        "--model",
        model,
        "--language",
        language,
        "--device-map",
        device_map,
        "--dtype",
        dtype,
        "--chunk-seconds",
        str(chunk_seconds),
        "--chunk-overlap-seconds",
        str(chunk_overlap_seconds),
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr[-2400:] or result.stdout[-2400:]
        raise RuntimeError(f"Qwen3-ASR failed using {python}: {detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument("--media", type=Path)
    parser.add_argument("--backend", choices=("auto", "qwen3-asr"), default="auto")
    parser.add_argument("--qwen-python")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--language",
        default="auto",
        help="Language name/code, or auto to let Qwen3-ASR identify the language",
    )
    parser.add_argument("--device-map", default="auto")
    parser.add_argument("--dtype", default="auto")
    parser.add_argument("--chunk-seconds", type=float, default=30.0)
    parser.add_argument("--chunk-overlap-seconds", type=float, default=1.5)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    job_dir = args.job.expanduser().resolve()
    access = read_json(job_dir / "metadata" / "access.json")
    if access.get("status") not in AUTHORIZED or access.get("secrets_stored") is True:
        parser.error("Authorized access must be recorded before ASR")
    glossary = read_json(job_dir / "terms" / "glossary.json")
    if not glossary.get("confirmed"):
        parser.error("The terminology glossary must be explicitly confirmed before ASR")
    glossary_hash = sha256_json(glossary.get("terms", []))
    if glossary.get("confirmed_hash") != glossary_hash:
        parser.error("The glossary changed after confirmation; confirm it again")

    context_path = job_dir / "terms" / "asr_context.json"
    context = read_json(context_path)
    prompt = build_asr_context(context)
    prompt_path = job_dir / "transcript" / "asr_prompt.txt"
    prompt_path.write_text(prompt + "\n", encoding="utf-8")

    qwen_python = find_qwen_python(args.qwen_python)
    if qwen_python is None:
        parser.error("No usable Qwen3-ASR environment was found; run scripts/setup_asr.py")

    try:
        media = resolve_media(job_dir, args.media)
        audio = job_dir / "transcript" / "asr_audio.wav"
        extract_audio(media, audio, args.force)
        register_managed_file(
            job_dir,
            audio,
            kind="derived_audio",
            origin="ffmpeg-asr",
        )
        backend_output = job_dir / "transcript" / "asr_backend_raw.json"
        run_metadata_path = job_dir / "transcript" / "asr_run.json"
        prior = read_json(run_metadata_path) if run_metadata_path.exists() else {}
        prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        cache_key = {
            "backend": "qwen3-asr",
            "model": args.model,
            "language": args.language,
            "chunk_seconds": args.chunk_seconds,
            "chunk_overlap_seconds": args.chunk_overlap_seconds,
            "prompt_sha256": prompt_sha,
            "glossary_hash": glossary_hash,
            "audio_sha256": sha256_file(audio),
            "audio_size": audio.stat().st_size,
        }
        cache_valid = (
            not args.force
            and backend_output.exists()
            and prior.get("cache_key") == cache_key
        )
        if not cache_valid:
            run_qwen(
                qwen_python,
                Path(__file__).resolve().parents[1],
                audio,
                backend_output,
                prompt_path,
                args.model,
                args.language,
                args.device_map,
                args.dtype,
                args.chunk_seconds,
                args.chunk_overlap_seconds,
            )
        payload = json.loads(backend_output.read_text(encoding="utf-8"))
        cues = segments_to_cues(payload)
        coverage = build_coverage_report(
            cues,
            probe_audio_duration(audio),
            payload.get("chunk_results"),
        )
        write_json(job_dir / "transcript" / "asr_coverage.json", coverage)
        generated = job_dir / "transcript" / "asr_generated.srt"
        generated.write_text(render_srt(cues), encoding="utf-8")
        normalized = normalize_transcript_file(job_dir, generated, "asr")
        write_json(
            run_metadata_path,
            {
                "cache_key": cache_key,
                "backend_python": str(qwen_python),
                "media": str(media),
                "generated_srt": str(generated.relative_to(job_dir)),
                "normalized_srt": str(normalized.relative_to(job_dir)),
                "coverage": str((job_dir / "transcript" / "asr_coverage.json").relative_to(job_dir)),
                "reused_backend_result": cache_valid,
            },
        )
        status = "needs_review" if coverage["failed_chunk_count"] or coverage["gap_ranges"] else "complete"
        update_pipeline_state(
            job_dir,
            "asr",
            status,
            f"{len(cues)} timestamped cues; coverage={coverage['coverage_ratio']:.1%}; "
            f"failed_chunks={coverage['failed_chunk_count']}",
        )
    except (FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    print(normalized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
