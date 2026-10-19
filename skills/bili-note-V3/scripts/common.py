#!/usr/bin/env python3
"""Shared helpers for the Bili Note V3 command-line tools."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import parse_qs, urlparse


BVID_RE = re.compile(r"(?i)\b(BV[0-9A-Za-z]{10})\b")
YOUTUBE_ID_RE = re.compile(r"^[0-9A-Za-z_-]{11}$")
SRT_TIME_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{3})"
)
EVIDENCE_MODE_FULL = "full"
EVIDENCE_MODE_AUDIO_ONLY = "audio_only"
EVIDENCE_MODES = frozenset({EVIDENCE_MODE_FULL, EVIDENCE_MODE_AUDIO_ONLY})
AUDIO_ONLY_DISCLOSURE = "音频优先整理，未核验画面内容"

# Access, source completeness, and browser handoff are deliberately separate
# dimensions.  The legacy ``status`` field remains in job packages for
# compatibility, but new code should use these canonical process states.
ACCESS_STATES = frozenset(
    {
        "unknown",
        "public_verified",
        "auth_required",
        "authenticated",
        "member_authorized",
        "denied",
        "drm_blocked",
    }
)
ACCESS_STATE_ALIASES = {
    "public": "public_verified",
    "public_verified": "public_verified",
    "authenticated": "authenticated",
    "member_authorized": "member_authorized",
    "auth_required": "auth_required",
    "denied": "denied",
    "drm_blocked": "drm_blocked",
    "unknown": "unknown",
}
AUTHORIZED_ACCESS_STATES = frozenset(
    {"public_verified", "authenticated", "member_authorized"}
)
SOURCE_STATES = frozenset(
    {"not_acquired", "complete", "preview_limited", "partial", "unverified", "failed"}
)
HANDOFF_STATES = frozenset(
    {"not_required", "pending", "ready", "unavailable", "failed"}
)
SOURCE_COVERAGE_THRESHOLD = 0.98
SOURCE_COVERAGE_TOLERANCE_SECONDS = 5.0


@dataclass
class Cue:
    index: int
    start: float | None
    end: float | None
    text: str


def evidence_mode(job: dict[str, Any]) -> str:
    """Return the evidence mode, treating schema 2.1 jobs as full-video jobs."""
    mode = str(job.get("policies", {}).get("evidence_mode") or EVIDENCE_MODE_FULL)
    if mode not in EVIDENCE_MODES:
        raise ValueError(f"Unsupported evidence mode: {mode}")
    return mode


def is_audio_only_job(job: dict[str, Any]) -> bool:
    return evidence_mode(job) == EVIDENCE_MODE_AUDIO_ONLY


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def canonical_access_state(access: dict[str, Any]) -> str:
    """Return the process-state value, accepting pre-V2 access records."""
    raw = access.get("access_state")
    if raw is None:
        raw = access.get("status")
    return ACCESS_STATE_ALIASES.get(str(raw or "unknown"), "unknown")


def legacy_access_status(access_state: str) -> str:
    """Map a canonical state to the legacy status field used by old scripts."""
    if access_state == "public_verified":
        return "public"
    return access_state


def source_coverage_report(
    declared_duration: float | int | None,
    acquired_duration: float | int | None,
    *,
    threshold: float = SOURCE_COVERAGE_THRESHOLD,
    tolerance_seconds: float = SOURCE_COVERAGE_TOLERANCE_SECONDS,
) -> dict[str, Any]:
    """Compare platform-declared duration with local media duration.

    A subtitle or ASR run over a preview file must never be allowed to masquerade
    as complete source evidence.  The small absolute tolerance handles normal
    container/encoder tail differences while the ratio protects short files.
    """
    try:
        declared = float(declared_duration or 0.0)
    except (TypeError, ValueError):
        declared = 0.0
    try:
        acquired = float(acquired_duration or 0.0)
    except (TypeError, ValueError):
        acquired = 0.0

    declared = max(0.0, declared)
    acquired = max(0.0, acquired)
    if declared <= 0.0 or acquired <= 0.0:
        return {
            "schema_version": "1.0",
            "declared_duration_seconds": round(declared, 3),
            "acquired_duration_seconds": round(acquired, 3),
            "missing_duration_seconds": None,
            "coverage_ratio": None,
            "threshold": threshold,
            "tolerance_seconds": tolerance_seconds,
            "complete": False,
            "state": "unverified",
            "reason": "declared_or_acquired_duration_missing",
        }

    missing = max(0.0, declared - acquired)
    ratio = min(1.0, acquired / declared)
    complete = missing <= tolerance_seconds or ratio >= threshold
    return {
        "schema_version": "1.0",
        "declared_duration_seconds": round(declared, 3),
        "acquired_duration_seconds": round(acquired, 3),
        "missing_duration_seconds": round(missing, 3),
        "coverage_ratio": round(ratio, 4),
        "threshold": threshold,
        "tolerance_seconds": tolerance_seconds,
        "complete": complete,
        "state": "complete" if complete else "preview_limited",
        "reason": "within_tolerance" if complete else "acquired_media_shorter_than_source",
    }


def update_access_record(
    job_dir: Path,
    *,
    access_state: str | None = None,
    source_state: str | None = None,
    handoff_state: str | None = None,
    browser_backend: str | None = None,
    verification: dict[str, Any] | None = None,
    source_coverage: dict[str, Any] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Update non-secret access dimensions without accepting browser secrets."""
    path = job_dir / "metadata" / "access.json"
    access = read_json(path) if path.exists() else {}
    if access_state is not None:
        if access_state not in ACCESS_STATES:
            raise ValueError(f"Unsupported access state: {access_state}")
        access["access_state"] = access_state
        access["status"] = legacy_access_status(access_state)
        access["entitlement"] = (
            "authorized" if access_state in AUTHORIZED_ACCESS_STATES else "not_authorized"
        )
    if source_state is not None:
        if source_state not in SOURCE_STATES:
            raise ValueError(f"Unsupported source state: {source_state}")
        access["source_state"] = source_state
    if handoff_state is not None:
        if handoff_state not in HANDOFF_STATES:
            raise ValueError(f"Unsupported handoff state: {handoff_state}")
        access["handoff_state"] = handoff_state
    if browser_backend is not None:
        access["browser_backend"] = browser_backend
    if verification is not None:
        access["verification"] = verification
    if source_coverage is not None:
        access["source_coverage"] = source_coverage
    if note is not None:
        access["notes"] = note
    access.setdefault("secrets_stored", False)
    access["updated_at"] = utc_now()
    write_json(path, access)
    return access


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Expected object at {path}:{line_number}")
            yield value


def sha256_json(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def register_managed_file(
    job_dir: Path,
    path: Path,
    *,
    kind: str,
    origin: str,
    cleanup_after_publish: bool = True,
) -> None:
    """Record a job-owned transient file that may be removed after publication."""
    job_dir = job_dir.resolve()
    raw_path = path.expanduser()
    if raw_path.is_symlink():
        raise ValueError(f"Managed media must not be a symlink: {raw_path}")
    path = raw_path.resolve()
    if not path.is_file():
        raise ValueError(f"Managed media must be a regular file: {path}")
    try:
        relative = path.relative_to(job_dir)
    except ValueError as exc:
        raise ValueError(f"Managed media must be inside the job directory: {path}") from exc

    manifest_path = job_dir / "metadata" / "media_manifest.json"
    manifest = (
        read_json(manifest_path)
        if manifest_path.exists()
        else {"schema_version": "1.0", "files": []}
    )
    files = manifest.setdefault("files", [])
    relative_text = relative.as_posix()
    row = next((item for item in files if item.get("path") == relative_text), None)
    value = {
        "path": relative_text,
        "kind": kind,
        "origin": origin,
        "managed": True,
        "cleanup_after_publish": cleanup_after_publish,
        "status": "present",
        "size": path.stat().st_size,
        "registered_at": utc_now(),
    }
    if row is None:
        files.append(value)
    else:
        row.update(value)
    manifest["updated_at"] = utc_now()
    write_json(manifest_path, manifest)


def extract_bvid(value: str) -> str:
    match = BVID_RE.search(value)
    if not match:
        raise ValueError("A Bilibili BV id was not found in the supplied URL or id")
    return match.group(1)


def validate_bilibili_source(value: str) -> tuple[str, str]:
    bvid = extract_bvid(value)
    if value.lower().startswith("bv"):
        return bvid, f"https://www.bilibili.com/video/{bvid}/"
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if host not in {"bilibili.com", "www.bilibili.com", "b23.tv"} and not host.endswith(
        ".bilibili.com"
    ):
        raise ValueError(f"Unsupported source host: {host or '(missing)'}")
    return bvid, value


def extract_youtube_id(value: str) -> str:
    raw = value.strip()
    if YOUTUBE_ID_RE.fullmatch(raw):
        return raw

    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if host in {"youtu.be", "www.youtu.be"}:
        candidate = parsed.path.strip("/").split("/", 1)[0]
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        parts = [part for part in parsed.path.split("/") if part]
        if parsed.path.rstrip("/") == "/watch":
            candidate = (parse_qs(parsed.query).get("v") or [""])[0]
        elif len(parts) == 2 and parts[0] in {"shorts", "live", "embed"}:
            candidate = parts[1]
        else:
            candidate = ""
    else:
        raise ValueError(f"Unsupported source host: {host or '(missing)'}")

    if not YOUTUBE_ID_RE.fullmatch(candidate):
        raise ValueError("A valid YouTube video id was not found in the supplied URL or id")
    return candidate


def validate_youtube_source(value: str) -> tuple[str, str]:
    video_id = extract_youtube_id(value)
    return video_id, f"https://www.youtube.com/watch?v={video_id}"


def validate_video_source(value: str) -> tuple[str, str, str]:
    """Return platform, stable source id, and a canonical single-video URL."""
    raw = value.strip()
    if BVID_RE.fullmatch(raw):
        source_id, url = validate_bilibili_source(raw)
        return "bilibili", source_id, url
    if YOUTUBE_ID_RE.fullmatch(raw):
        source_id, url = validate_youtube_source(raw)
        return "youtube", source_id, url

    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if host in {"bilibili.com", "www.bilibili.com", "b23.tv"} or host.endswith(
        ".bilibili.com"
    ):
        source_id, url = validate_bilibili_source(raw)
        return "bilibili", source_id, url
    if host in {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
        "www.youtu.be",
    }:
        source_id, url = validate_youtube_source(raw)
        return "youtube", source_id, url
    raise ValueError(f"Unsupported source host: {host or '(missing)'}")


def job_source_identity(
    job: dict[str, Any], metadata: dict[str, Any] | None = None
) -> tuple[str, str]:
    source = job.get("source", {})
    platform_name = str(source.get("platform") or "bilibili").strip().lower()
    source_id = str(
        source.get("id")
        or source.get("bvid")
        or (metadata or {}).get("id")
        or ""
    ).strip()
    if platform_name not in {"bilibili", "youtube"}:
        raise ValueError(f"Unsupported source platform: {platform_name or '(missing)'}")
    if not source_id:
        raise ValueError("Job source is missing a stable source id")
    return platform_name, source_id


def parse_timestamp(value: str) -> float:
    hours, minutes, seconds_ms = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds_ms)


def format_timestamp(seconds: float | None, separator: str = ",") -> str:
    if seconds is None:
        return "00:00:00,000"
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}{separator}{millis:03d}"


def parse_srt(text: str) -> list[Cue]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", normalized):
        lines = [line.strip("\ufeff") for line in block.splitlines()]
        if not lines:
            continue
        time_index = next((i for i, line in enumerate(lines) if SRT_TIME_RE.search(line)), None)
        if time_index is None:
            continue
        match = SRT_TIME_RE.search(lines[time_index])
        assert match is not None
        try:
            cue_index = int(lines[0].strip()) if time_index > 0 else len(cues) + 1
        except ValueError:
            cue_index = len(cues) + 1
        cue_text = "\n".join(lines[time_index + 1 :]).strip()
        cues.append(
            Cue(
                index=cue_index,
                start=parse_timestamp(match.group("start")),
                end=parse_timestamp(match.group("end")),
                text=cue_text,
            )
        )
    return cues


def parse_text_lines(text: str) -> list[Cue]:
    return [
        Cue(index=index, start=None, end=None, text=line.strip())
        for index, line in enumerate(text.splitlines(), start=1)
        if line.strip()
    ]


def render_srt(cues: Iterable[Cue]) -> str:
    blocks: list[str] = []
    for index, cue in enumerate(cues, start=1):
        blocks.append(
            f"{index}\n{format_timestamp(cue.start)} --> {format_timestamp(cue.end)}\n{cue.text}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def update_pipeline_state(job_dir: Path, phase: str, status: str, detail: str = "") -> None:
    path = job_dir / "metadata" / "pipeline_state.json"
    state = read_json(path)
    phases = state.setdefault("phases", {})
    phases[phase] = {"status": status, "detail": detail, "updated_at": utc_now()}
    state["updated_at"] = utc_now()
    write_json(path, state)
