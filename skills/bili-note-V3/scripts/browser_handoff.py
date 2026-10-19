#!/usr/bin/env python3
"""Request or record a safe Edge/Chrome browser handoff.

The browser remains the owner of login state and signed playback requests.  This
CLI accepts only a local media copy and non-secret verification facts; it never
accepts cookies, browser profiles, authorization headers, or remote media URLs.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from common import (
    ACCESS_STATE_ALIASES,
    AUTHORIZED_ACCESS_STATES,
    canonical_access_state,
    is_audio_only_job,
    job_source_identity,
    read_json,
    register_managed_file,
    source_coverage_report,
    update_access_record,
    update_pipeline_state,
    utc_now,
    write_json,
)


BACKENDS = ("edge", "chrome", "generic_browser")
BACKEND_ALIASES = {
    "edge": "edge",
    "msedge": "edge",
    "microsoft-edge": "edge",
    "chrome": "chrome",
    "google-chrome": "chrome",
    "generic": "generic_browser",
    "generic_browser": "generic_browser",
}
BACKEND_LABELS = {
    "edge": "Microsoft Edge",
    "chrome": "Google Chrome",
    "generic_browser": "Edge 或 Chrome",
}
ACCESS_CHOICES = ("public", "public_verified", "authenticated", "member_authorized")
SECRET_TEXT_RE = re.compile(
    r"cookie|sessdata|authorization|bearer|password|local\s+storage|session\s+database|"
    r"(?:access[_-]?token|refresh[_-]?token|signed[_-]?url|signature|token|expires)\s*[=:]",
    re.I,
)


def reject_secret_text(value: str, label: str = "value") -> None:
    if SECRET_TEXT_RE.search(value or ""):
        raise ValueError(f"{label} appears to contain browser authentication material")


def parse_playback_points(values: list[str]) -> list[float]:
    points: list[float] = []
    for raw in values:
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid playback timestamp: {raw}") from exc
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid playback timestamp: {raw}")
        points.append(round(value, 3))
    return sorted(set(points))


def browser_access_state(value: str) -> str:
    state = ACCESS_STATE_ALIASES.get(value, value)
    if state not in AUTHORIZED_ACCESS_STATES:
        raise ValueError(f"Browser handoff requires an authorized access state, got {value}")
    return state


def normalize_backend(value: str) -> str:
    backend = BACKEND_ALIASES.get(str(value or "").strip().lower())
    if backend not in BACKENDS:
        raise ValueError(
            f"Unsupported browser backend: {value}; choose edge, chrome, or generic_browser"
        )
    return backend


def probe_duration(media: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe is required to verify browser-handoff media")
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(media),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout or "{}")
    try:
        duration = float(payload.get("format", {}).get("duration") or 0.0)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"ffprobe returned an invalid duration for {media}") from exc
    if duration <= 0:
        raise RuntimeError(f"ffprobe returned no usable duration for {media}")
    return duration


def copy_media_into_job(job_dir: Path, source: Path, audio_only: bool) -> Path:
    source = source.expanduser()
    if source.is_symlink():
        raise ValueError("Browser-handoff media must not be a symlink")
    if not source.is_file():
        raise ValueError(f"Browser-handoff media is not a regular file: {source}")

    try:
        resolved = source.resolve()
        inside_job = resolved.is_relative_to(job_dir.resolve())
    except (OSError, ValueError):
        inside_job = False
        resolved = source

    if inside_job:
        destination = resolved
    else:
        suffix = source.suffix.lower() or (".m4a" if audio_only else ".mp4")
        prefix = "audio.browser" if audio_only else "video.browser"
        destination = job_dir / "source" / f"{prefix}{suffix}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return destination.resolve()


def build_handoff_record(
    *,
    backend: str,
    access_state: str,
    playback_points: list[float],
    near_end_verified: bool,
    handoff_state: str,
    source_state: str,
    media_path: str | None = None,
    source_coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the persisted, non-secret browser handoff record."""
    return {
        "schema_version": "1.0",
        "backend": backend,
        "access_state": access_state,
        "handoff_state": handoff_state,
        "source_state": source_state,
        "verification": {
            "method": "browser_visible_playback",
            "playback_points_seconds": playback_points,
            "near_end_verified": bool(near_end_verified),
            "verified_at": utc_now(),
        },
        "media_path": media_path,
        "source_coverage": source_coverage,
        "secrets_stored": False,
    }


def canonical_video_url(job: dict[str, Any]) -> str:
    platform_name, source_id = job_source_identity(job)
    if platform_name == "bilibili":
        return f"https://www.bilibili.com/video/{source_id}/"
    return f"https://www.youtube.com/watch?v={source_id}"


def request_handoff(job_dir: Path, backend: str) -> dict[str, Any]:
    backend = normalize_backend(backend)
    job = read_json(job_dir / "metadata" / "job.json")
    current = read_json(job_dir / "metadata" / "access.json")
    current_state = canonical_access_state(current)
    requested_state = (
        current_state
        if current_state in {"authenticated", "member_authorized", "denied", "drm_blocked"}
        else "auth_required"
    )
    request = {
        "schema_version": "1.0",
        "state": "pending",
        "backend": backend,
        "url": canonical_video_url(job),
        "requested_at": utc_now(),
        "supported_backends": list(BACKENDS),
        "next_action": (
            f"在已登录的 {BACKEND_LABELS[backend]} 中打开 exact URL，确认可播放目标视频并完成一次接近末尾的播放校验；"
            "随后由浏览器连接器提交非敏感媒体副本。不要导出 Cookie、Local Storage 或授权头。"
        ),
        "secrets_stored": False,
    }
    write_json(job_dir / "metadata" / "browser_handoff_request.json", request)
    update_access_record(
        job_dir,
        access_state=requested_state,
        handoff_state="pending",
        browser_backend=backend,
    )
    update_pipeline_state(job_dir, "access", "blocked", "browser handoff pending")
    return request


def record_handoff(
    job_dir: Path,
    *,
    backend: str,
    access_state: str,
    playback_points: list[str],
    near_end_verified: bool,
    media_path: Path | None,
    declared_duration: float | None,
    note: str,
) -> dict[str, Any]:
    reject_secret_text(note, "--note")
    state = browser_access_state(access_state)
    points = parse_playback_points(playback_points)
    if not points and not near_end_verified:
        raise ValueError(
            "Browser handoff requires --playback-at or --near-end to prove visible playback"
        )

    job = read_json(job_dir / "metadata" / "job.json")
    audio_only = is_audio_only_job(job)
    copied_media: Path | None = None
    coverage: dict[str, Any] | None = None
    handoff_state = "unavailable"
    source_state = "not_acquired"

    if media_path is not None:
        copied_media = copy_media_into_job(job_dir, media_path, audio_only)
        kind = "source_audio" if audio_only else "source_video"
        register_managed_file(
            job_dir,
            copied_media,
            kind=kind,
            origin=f"browser-handoff-{backend}",
        )
        metadata = (
            read_json(job_dir / "metadata" / "source_metadata.json")
            if (job_dir / "metadata" / "source_metadata.json").exists()
            else {}
        )
        declared = declared_duration if declared_duration is not None else metadata.get("duration")
        if declared is None:
            raise ValueError(
                "Browser handoff media requires --declared-duration when source metadata has no duration"
            )
        coverage = source_coverage_report(declared, probe_duration(copied_media))
        coverage["media_path"] = str(copied_media.relative_to(job_dir))
        write_json(job_dir / "metadata" / "source_coverage.json", coverage)
        source_state = str(coverage["state"])
        handoff_state = "ready" if coverage["complete"] else "pending"

    record = build_handoff_record(
        backend=backend,
        access_state=state,
        playback_points=points,
        near_end_verified=near_end_verified,
        handoff_state=handoff_state,
        source_state=source_state,
        media_path=str(copied_media.relative_to(job_dir)) if copied_media else None,
        source_coverage=coverage,
    )
    if note:
        record["note"] = note
    write_json(job_dir / "metadata" / "browser_handoff.json", record)
    update_access_record(
        job_dir,
        access_state=state,
        source_state=source_state,
        handoff_state=handoff_state,
        browser_backend=backend,
        verification=record["verification"],
        source_coverage=coverage,
        note=note,
    )
    if coverage and coverage["complete"]:
        update_pipeline_state(job_dir, "access", "complete", "browser handoff media verified")
    else:
        update_pipeline_state(job_dir, "access", "blocked", "browser handoff media is unavailable or incomplete")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    request_parser = subparsers.add_parser("request", help="Create a safe user-action request")
    request_parser.add_argument("job", type=Path)
    request_parser.add_argument("--backend", required=True)

    record_parser = subparsers.add_parser("record", help="Record non-secret browser verification")
    record_parser.add_argument("job", type=Path)
    record_parser.add_argument("--backend", required=True)
    record_parser.add_argument("--access-state", choices=ACCESS_CHOICES, required=True)
    record_parser.add_argument("--playback-at", action="append", default=[])
    record_parser.add_argument("--near-end", action="store_true")
    record_parser.add_argument("--media-path", type=Path)
    record_parser.add_argument("--declared-duration", type=float)
    record_parser.add_argument("--note", default="")

    args = parser.parse_args()
    job_dir = args.job.expanduser().resolve()
    try:
        backend = normalize_backend(args.backend)
        if args.command == "request":
            result = request_handoff(job_dir, backend)
        else:
            result = record_handoff(
                job_dir,
                backend=backend,
                access_state=args.access_state,
                playback_points=args.playback_at,
                near_end_verified=args.near_end,
                media_path=args.media_path,
                declared_duration=args.declared_duration,
                note=args.note,
            )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
