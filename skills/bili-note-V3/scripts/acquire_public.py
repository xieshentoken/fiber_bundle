#!/usr/bin/env python3
"""Acquire public Bilibili or YouTube material via yt-dlp."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from common import (
    AUTHORIZED_ACCESS_STATES,
    canonical_access_state,
    is_audio_only_job,
    job_source_identity,
    read_json,
    register_managed_file,
    source_coverage_report,
    update_access_record,
    update_pipeline_state,
    write_json,
)


def yt_dlp() -> str:
    path = shutil.which("yt-dlp")
    if not path:
        raise RuntimeError("yt-dlp is required for public source acquisition")
    return path


def public_yt_dlp_args() -> list[str]:
    return [
        yt_dlp(),
        "--ignore-config",
        "--no-cookies",
        "--no-cookies-from-browser",
        "--no-write-comments",
        "--no-playlist",
        "--no-write-playlist-metafiles",
    ]


def source_download_format(audio_only: bool) -> str:
    """Select a source format without allowing audio-only jobs to fetch video."""
    return "ba" if audio_only else "bv*+ba/b"


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = completed.stderr[-2000:] or completed.stdout[-2000:] or "yt-dlp failed"
        safe_detail = re.sub(r"https?://\S+", "<url>", detail).strip()
        raise RuntimeError(f"yt-dlp failed: {safe_detail}")
    return completed


def probe_duration(media: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe is required to verify source media completeness")
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


def record_source_coverage(job_dir: Path, media: Path) -> dict[str, Any]:
    """Persist source coverage and move the job into a safe next state."""
    metadata_path = job_dir / "metadata" / "source_metadata.json"
    metadata = read_json(metadata_path) if metadata_path.exists() else {}
    report = source_coverage_report(metadata.get("duration"), probe_duration(media))
    report["media_path"] = str(media.relative_to(job_dir))
    write_json(job_dir / "metadata" / "source_coverage.json", report)

    access = read_json(job_dir / "metadata" / "access.json")
    current_state = canonical_access_state(access)
    if report["complete"]:
        handoff_state = access.get("handoff_state") or (
            "not_required" if current_state == "public_verified" else "ready"
        )
        update_access_record(
            job_dir,
            access_state=current_state,
            source_state="complete",
            handoff_state=handoff_state,
            source_coverage=report,
        )
        update_pipeline_state(job_dir, "access", "complete", "source media coverage verified")
    else:
        # A short public download is treated as an access boundary, not as a
        # successful public extraction.  The user can then finish the browser
        # handoff in Edge/Chrome without any cookie fallback.
        next_state = (
            "auth_required"
            if current_state in {"unknown", "public_verified"}
            else current_state
        )
        update_access_record(
            job_dir,
            access_state=next_state,
            source_state=str(report["state"]),
            handoff_state="pending" if next_state == "auth_required" else "unavailable",
            source_coverage=report,
        )
        update_pipeline_state(
            job_dir,
            "access",
            "blocked",
            f"source coverage incomplete: {report.get('coverage_ratio')}",
        )
    return report


def usable_subtitle_languages(payload: dict[str, Any], key: str) -> list[str]:
    return sorted(
        language
        for language in (payload.get(key) or {})
        if not any(
            forbidden in language.casefold()
            for forbidden in ("danmaku", "live_chat", "comment")
        )
    )


def sanitized_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    human_languages = usable_subtitle_languages(payload, "subtitles")
    automatic_languages = usable_subtitle_languages(payload, "automatic_captions")
    return {
        "id": payload.get("id"),
        "title": payload.get("title"),
        "description": payload.get("description"),
        "duration": payload.get("duration"),
        "uploader": payload.get("uploader"),
        "uploader_id": payload.get("uploader_id"),
        "channel": payload.get("channel"),
        "channel_id": payload.get("channel_id"),
        "upload_date": payload.get("upload_date"),
        "timestamp": payload.get("timestamp"),
        "language": payload.get("language"),
        "live_status": payload.get("live_status"),
        "is_live": bool(payload.get("is_live")),
        "availability": payload.get("availability"),
        "chapters": payload.get("chapters") or [],
        "playlist_count": payload.get("playlist_count"),
        "human_subtitle_languages": human_languages,
        "automatic_caption_languages": automatic_languages,
        "subtitle_languages": sorted(set(human_languages) | set(automatic_languages)),
        "authentication_secrets_stored": False,
        "danmaku_stored": False,
        "comments_stored": False,
        "live_chat_stored": False,
    }


def acquire_metadata(job_dir: Path, url: str) -> dict[str, Any]:
    completed = run(
        public_yt_dlp_args()
        + [
            "--dump-single-json",
            "--skip-download",
            "--no-warnings",
            url,
        ]
    )
    payload = json.loads(completed.stdout)
    metadata = sanitized_metadata(payload)
    job = read_json(job_dir / "metadata" / "job.json")
    _, expected_id = job_source_identity(job)
    if str(metadata.get("id") or "") != expected_id:
        raise RuntimeError(
            f"yt-dlp returned source id {metadata.get('id')!r}; expected {expected_id!r}"
        )
    write_json(job_dir / "metadata" / "source_metadata.json", metadata)
    return metadata


def language_matches(tag: str, language: str) -> bool:
    left = tag.casefold().replace("_", "-")
    right = language.casefold().replace("_", "-")
    return left == right or left.startswith(right + "-")


def first_language(languages: list[str], preferences: list[str]) -> str | None:
    for preference in preferences:
        match = next(
            (language for language in languages if language_matches(language, preference)),
            None,
        )
        if match:
            return match
    return None


def select_subtitle_track(
    metadata: dict[str, Any], preferred_languages: list[str] | None = None
) -> dict[str, str] | None:
    human = [str(value) for value in metadata.get("human_subtitle_languages", [])]
    automatic = [str(value) for value in metadata.get("automatic_caption_languages", [])]
    source_language = str(metadata.get("language") or "").strip()
    preferred = preferred_languages or ["zh-Hans", "zh-Hant", "zh", "en"]

    language = first_language(human, [source_language] if source_language else [])
    language = language or first_language(human, preferred)
    language = language or (human[0] if human else None)
    if language:
        return {"kind": "human", "language": language}

    automatic_source_preferences = (
        [f"{source_language}-orig", source_language] if source_language else []
    )
    language = first_language(automatic, automatic_source_preferences)
    language = language or first_language(automatic, preferred)
    language = language or (automatic[0] if automatic else None)
    if language:
        return {"kind": "automatic", "language": language}
    return None


def youtube_live_is_unsupported(job: dict[str, Any], metadata: dict[str, Any]) -> bool:
    platform_name, _ = job_source_identity(job, metadata)
    return platform_name == "youtube" and (
        metadata.get("is_live") is True
        or metadata.get("live_status") in {"is_live", "is_upcoming", "post_live"}
    )


def acquire_subtitles(
    job_dir: Path, url: str, preferred_languages: list[str] | None = None
) -> dict[str, Any]:
    output_dir = job_dir / "transcript" / "source"
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = job_dir / "metadata" / "source_metadata.json"
    if not metadata_path.exists():
        raise RuntimeError("Acquire metadata before selecting a subtitle track")
    selection = select_subtitle_track(read_json(metadata_path), preferred_languages)
    selection_path = job_dir / "transcript" / "subtitle_selection.json"
    if selection is None:
        result = {
            "schema_version": "1.0",
            "status": "absent",
            "kind": None,
            "language": None,
            "files": [],
        }
        write_json(selection_path, result)
        return result

    subtitle_flags = (
        ["--write-subs", "--no-write-auto-subs"]
        if selection["kind"] == "human"
        else ["--no-write-subs", "--write-auto-subs"]
    )
    run(
        public_yt_dlp_args()
        + [
            "--skip-download",
            *subtitle_flags,
            "--sub-langs",
            re.escape(selection["language"]),
            "--sub-format",
            "srt/best",
            "--paths",
            str(output_dir),
            "--output",
            "%(id)s.%(ext)s",
            url,
        ]
    )
    language_marker = f".{selection['language']}."
    files = sorted(
        path.relative_to(job_dir).as_posix()
        for path in output_dir.iterdir()
        if path.is_file() and language_marker in path.name
    )
    result = {
        "schema_version": "1.0",
        "status": "downloaded" if files else "missing_after_download",
        "kind": selection["kind"],
        "language": selection["language"],
        "files": files,
    }
    write_json(selection_path, result)
    return result


def acquire_media(job_dir: Path, url: str) -> dict[str, Any]:
    output_dir = job_dir / "source"
    output_dir.mkdir(parents=True, exist_ok=True)
    run(
        public_yt_dlp_args()
        + [
            "--no-write-subs",
            "--format",
            source_download_format(False),
            "--merge-output-format",
            "mp4",
            "--paths",
            str(output_dir),
            "--output",
            "video.%(ext)s",
            url,
        ]
    )
    media_files = sorted(output_dir.glob("video.*"))
    if len(media_files) != 1:
        raise RuntimeError(f"Expected exactly one downloaded video, found {len(media_files)}")
    register_managed_file(
        job_dir,
        media_files[0],
        kind="source_video",
        origin="yt-dlp-public",
    )
    return record_source_coverage(job_dir, media_files[0])


def acquire_audio(job_dir: Path, url: str) -> dict[str, Any]:
    """Download only the best audio stream; never fall back to video."""
    output_dir = job_dir / "source"
    output_dir.mkdir(parents=True, exist_ok=True)
    run(
        public_yt_dlp_args()
        + [
            "--no-write-subs",
            "--format",
            source_download_format(True),
            "--paths",
            str(output_dir),
            "--output",
            "audio.%(ext)s",
            url,
        ]
    )
    media_files = sorted(path for path in output_dir.glob("audio.*") if path.is_file())
    if len(media_files) != 1:
        raise RuntimeError(f"Expected exactly one downloaded audio file, found {len(media_files)}")
    register_managed_file(
        job_dir,
        media_files[0],
        kind="source_audio",
        origin="yt-dlp-public-audio",
    )
    return record_source_coverage(job_dir, media_files[0])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument(
        "--mode",
        choices=("metadata", "subtitles", "media", "audio", "all"),
        default="all",
    )
    parser.add_argument(
        "--subtitle-language",
        action="append",
        default=[],
        help="Preferred subtitle language; repeat for priority order",
    )
    args = parser.parse_args()

    job_dir = args.job.expanduser().resolve()
    access = read_json(job_dir / "metadata" / "access.json")
    if canonical_access_state(access) != "public_verified":
        parser.error(
            "This command is intentionally limited to public videos. Use the authenticated browser handoff for login/member access."
        )
    job = read_json(job_dir / "metadata" / "job.json")
    url = job["source"]["url"]
    audio_only = is_audio_only_job(job)
    if args.mode == "media" and audio_only:
        parser.error("Audio-only jobs must acquire audio, not video media")
    if args.mode == "audio" and not audio_only:
        parser.error("The audio acquisition mode requires --evidence-mode audio-only at job initialization")

    metadata: dict[str, Any] | None = None
    if args.mode in {"metadata", "all"}:
        metadata = acquire_metadata(job_dir, url)
    elif (job_dir / "metadata" / "source_metadata.json").exists():
        metadata = read_json(job_dir / "metadata" / "source_metadata.json")
    if metadata is not None and youtube_live_is_unsupported(job, metadata):
        parser.error("Active, upcoming, or still-processing YouTube live streams are not supported")
    if args.mode in {"subtitles", "all"}:
        acquire_subtitles(job_dir, url, args.subtitle_language or None)
    coverage: dict[str, Any] | None = None
    if args.mode == "media" or (args.mode == "all" and not audio_only):
        coverage = acquire_media(job_dir, url)
    if args.mode == "audio" or (args.mode == "all" and audio_only):
        coverage = acquire_audio(job_dir, url)
    if coverage is not None and not coverage.get("complete"):
        parser.error(
            "Source media is incomplete; continue through the authenticated browser handoff "
            "instead of processing the preview."
        )
    source_kind = "audio" if audio_only else "video"
    update_pipeline_state(
        job_dir,
        "access",
        "complete",
        f"Public acquisition mode: {args.mode}; source={source_kind}",
    )
    print(job_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
