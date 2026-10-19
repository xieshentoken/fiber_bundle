#!/usr/bin/env python3
"""Select adaptive, timestamped keyframes for full-evidence video jobs.

The selector keeps the old interval + scene-change strategy as its balanced
profile, adds a cheap I-frame reconnaissance profile, supports focused ranges
and transcript-cue timestamps, and can append a focused pass to an existing
manifest without invalidating prior frame IDs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from common import is_audio_only_job, read_json, update_pipeline_state, write_json


PTS_RE = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")
FRAME_ID_RE = re.compile(r"^F(\d+)$")

PROFILE_DEFAULTS: dict[str, dict[str, Any]] = {
    "quick": {
        "interval": 30.0,
        "scene_threshold": 0.32,
        "max_frames": 60,
        "max_width": 768,
        "engine": "iframe",
    },
    "balanced": {
        "interval": 15.0,
        "scene_threshold": 0.32,
        "max_frames": 120,
        "max_width": 1600,
        "engine": "interval_scene",
    },
    "deep": {
        "interval": 10.0,
        "scene_threshold": 0.28,
        "max_frames": 240,
        "max_width": 1600,
        "engine": "interval_scene",
    },
}


def command_path(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"Required executable is missing: {name}")
    return path


def probe_duration(video: Path) -> float:
    command = [
        command_path("ffprobe"),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(video),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        if not list(output.glob("frame_*.jpg")) and (
            "Nothing was written" in completed.stderr or "received no packets" in completed.stderr
        ):
            return []
        raise RuntimeError(f"ffmpeg frame extraction failed: {completed.stderr[-1600:]}")
    payload = json.loads(completed.stdout)
    return float(payload["format"]["duration"])


def parse_time(value: str | float | int | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (float, int)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    parts = text.split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError as exc:
        raise ValueError(f"Invalid timestamp: {value!r}") from exc
    raise ValueError(f"Invalid timestamp: {value!r}")


def parse_timestamps(value: str | None) -> list[float]:
    if not value:
        return []
    points: list[float] = []
    for token in value.split(","):
        token = token.strip()
        if token:
            points.append(round(parse_time(token) or 0.0, 3))
    return sorted(set(points))


def _scope_args(start_seconds: float | None, end_seconds: float | None) -> list[str]:
    args: list[str] = []
    if start_seconds is not None:
        args.extend(["-ss", f"{start_seconds:.3f}"])
    if end_seconds is not None:
        start = start_seconds or 0.0
        args.extend(["-t", f"{max(0.0, end_seconds - start):.3f}"])
    return args


def extract_frames(
    video: Path,
    output: Path,
    filter_graph: str,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
) -> list[float]:
    output.mkdir(parents=True, exist_ok=True)
    command = [command_path("ffmpeg"), "-hide_banner", "-loglevel", "info"]
    if start_seconds is not None:
        command.extend(["-ss", f"{start_seconds:.3f}"])
    command.extend(
        [
            "-i",
            str(video),
        ]
    )
    if end_seconds is not None:
        start = start_seconds or 0.0
        command.extend(["-t", f"{max(0.0, end_seconds - start):.3f}"])
    command.extend(
        [
            "-vf",
            filter_graph,
            "-fps_mode",
            "vfr",
            "-q:v",
            "2",
            str(output / "frame_%06d.jpg"),
        ]
    )
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        if not list(output.glob("frame_*.jpg")) and (
            "Nothing was written" in completed.stderr or "received no packets" in completed.stderr
        ):
            return []
        raise RuntimeError(f"ffmpeg I-frame extraction failed: {completed.stderr[-1600:]}")
    offset = start_seconds or 0.0
    return [round(offset + float(match.group(1)), 3) for match in PTS_RE.finditer(completed.stderr)]


def extract_iframe_candidates(
    video: Path,
    output: Path,
    max_width: int,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
) -> list[float]:
    output.mkdir(parents=True, exist_ok=True)
    command = [command_path("ffmpeg"), "-hide_banner", "-loglevel", "info"]
    if start_seconds is not None:
        command.extend(["-ss", f"{start_seconds:.3f}"])
    command.extend(["-skip_frame", "nokey", "-i", str(video)])
    if end_seconds is not None:
        start = start_seconds or 0.0
        command.extend(["-t", f"{max(0.0, end_seconds - start):.3f}"])
    scale = f"scale=w='min({max_width},iw)':h=-2"
    command.extend(
        [
            "-vf",
            f"showinfo,{scale}",
            "-fps_mode",
            "vfr",
            "-q:v",
            "2",
            str(output / "frame_%06d.jpg"),
        ]
    )
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    offset = start_seconds or 0.0
    return [round(offset + float(match.group(1)), 3) for match in PTS_RE.finditer(completed.stderr)]


def extract_one_frame(video: Path, output: Path, timestamp: float, max_width: int) -> bool:
    output.parent.mkdir(parents=True, exist_ok=True)
    scale = f"scale=w='min({max_width},iw)':h=-2"
    command = [
        command_path("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{timestamp:.3f}",
        "-i",
        str(video),
        "-frames:v",
        "1",
        "-vf",
        scale,
        "-q:v",
        "2",
        str(output),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    return completed.returncode == 0 and output.is_file()


def average_hash(image: Path) -> str:
    command = [
        command_path("ffmpeg"),
        "-v",
        "error",
        "-i",
        str(image),
        "-vf",
        "scale=16:16,format=gray",
        "-f",
        "rawvideo",
        "-",
    ]
    completed = subprocess.run(command, check=True, capture_output=True)
    pixels = completed.stdout
    if len(pixels) != 256:
        raise RuntimeError(f"Unexpected hash image size for {image}: {len(pixels)}")
    mean = sum(pixels) / len(pixels)
    bits = "".join("1" if value >= mean else "0" for value in pixels)
    return f"{int(bits, 2):064x}"


def batch_average_hashes(images: list[Path]) -> list[str]:
    """Compute all 16x16 hashes in one ffmpeg pass, with a safe fallback."""
    if not images:
        return []
    try:
        with tempfile.TemporaryDirectory(prefix="bili_note_v2_hash_") as directory:
            sequence = Path(directory)
            for index, image in enumerate(images):
                target = sequence / f"frame_{index:06d}.jpg"
                try:
                    os.link(image, target)
                except OSError:
                    shutil.copy2(image, target)
            command = [
                command_path("ffmpeg"),
                "-v",
                "error",
                "-start_number",
                "0",
                "-i",
                str(sequence / "frame_%06d.jpg"),
                "-vf",
                "scale=16:16,format=gray",
                "-f",
                "rawvideo",
                "-",
            ]
            completed = subprocess.run(command, check=True, capture_output=True)
            raw = completed.stdout
            if len(raw) != 256 * len(images):
                raise RuntimeError("batch hash output did not match candidate count")
            hashes: list[str] = []
            for offset in range(0, len(raw), 256):
                pixels = raw[offset : offset + 256]
                mean = sum(pixels) / len(pixels)
                bits = "".join("1" if value >= mean else "0" for value in pixels)
                hashes.append(f"{int(bits, 2):064x}")
            return hashes
    except (OSError, RuntimeError, subprocess.CalledProcessError):
        return [average_hash(image) for image in images]


def hamming_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def evenly_spaced(rows: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if count <= 0 or not rows:
        return []
    if len(rows) <= count:
        return rows
    if count == 1:
        return [rows[len(rows) // 2]]
    positions = {round(index * (len(rows) - 1) / (count - 1)) for index in range(count)}
    return [rows[index] for index in sorted(positions)]


def select_budget(rows: list[dict[str, Any]], maximum: int) -> list[dict[str, Any]]:
    """Reserve most of the budget for scene frames and retain interval coverage."""
    if len(rows) <= maximum:
        return rows
    scene = [row for row in rows if row["source"] == "scene"]
    interval = [row for row in rows if row["source"] == "interval"]
    other = [row for row in rows if row["source"] not in {"scene", "interval"}]
    if not scene and not interval:
        return evenly_spaced(rows, maximum)
    scene_budget = min(len(scene), max(1, round(maximum * 0.7)))
    selected = evenly_spaced(scene, scene_budget)
    remaining = maximum - len(selected)
    selected.extend(evenly_spaced(interval, remaining))
    remaining = maximum - len(selected)
    if remaining > 0:
        selected.extend(evenly_spaced(other, remaining))
    return sorted(selected, key=lambda row: row["timestamp"])


def _source_priority(source: str) -> int:
    return {"cue": 0, "scene": 1, "iframe": 2, "interval": 3}.get(source, 4)


def deduplicate_rows(
    rows: list[dict[str, Any]], interval: float, dedupe_hamming: int, job_dir: Path | None = None
) -> tuple[list[dict[str, Any]], int]:
    rows = sorted(rows, key=lambda row: (row["timestamp"], _source_priority(row["source"])))
    paths = [
        (job_dir / row["candidate_path"]).resolve() if job_dir else Path(row["candidate_path"])
        for row in rows
    ]
    hashes = batch_average_hashes(paths)
    for row, value in zip(rows, hashes, strict=True):
        row["average_hash"] = value

    deduplicated: list[dict[str, Any]] = []
    dropped = 0
    for row in rows:
        if not deduplicated:
            deduplicated.append(row)
            continue
        previous = deduplicated[-1]
        same_moment = abs(row["timestamp"] - previous["timestamp"]) <= 0.5
        near_duplicate = hamming_distance(row["average_hash"], previous["average_hash"]) <= dedupe_hamming
        interval_duplicate = (
            near_duplicate
            and row["source"] == "interval"
            and row.get("reason") != "scope-endpoint"
            and previous.get("reason") != "scope-endpoint"
            and row["timestamp"] - previous["timestamp"] < interval * 1.5
        )
        if same_moment or interval_duplicate:
            if _source_priority(row["source"]) < _source_priority(previous["source"]):
                deduplicated[-1] = row
            dropped += 1
            continue
        deduplicated.append(row)
    return deduplicated, dropped


def select_with_pins(rows: list[dict[str, Any]], maximum: int) -> tuple[list[dict[str, Any]], bool]:
    pinned = [row for row in rows if row.get("source") == "cue"]
    regular = [row for row in rows if row.get("source") != "cue"]
    if len(pinned) >= maximum:
        return sorted(pinned, key=lambda row: row["timestamp"]), len(pinned) > maximum
    selected = pinned + select_budget(regular, maximum - len(pinned))
    return sorted(selected, key=lambda row: row["timestamp"]), False


def max_temporal_gap(
    rows: list[dict[str, Any]], start_seconds: float, end_seconds: float
) -> float:
    points = [start_seconds, end_seconds, *(float(row["timestamp"]) for row in rows)]
    points = sorted(set(round(point, 3) for point in points if start_seconds <= point <= end_seconds))
    return round(max((right - left for left, right in zip(points, points[1:])), default=end_seconds - start_seconds), 3)


def existing_manifest(job_dir: Path) -> dict[str, Any]:
    path = job_dir / "frames" / "frame_manifest.json"
    return read_json(path) if path.exists() else {}


def next_frame_number(rows: list[dict[str, Any]]) -> int:
    numbers = []
    for row in rows:
        match = FRAME_ID_RE.match(str(row.get("frame_id", "")))
        if match:
            numbers.append(int(match.group(1)))
    return max(numbers, default=0) + 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--profile", "--scan-profile", dest="profile", choices=tuple(PROFILE_DEFAULTS), default="balanced")
    parser.add_argument("--interval", type=float)
    parser.add_argument("--scene-threshold", type=float)
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--max-width", type=int)
    parser.add_argument("--dedupe-hamming", type=int, default=2)
    parser.add_argument("--start", type=str)
    parser.add_argument("--end", type=str)
    parser.add_argument("--timestamps", type=str, help="Comma-separated absolute cue timestamps")
    parser.add_argument("--append", action="store_true", help="Merge this focused pass into the existing frame manifest")
    args = parser.parse_args()

    defaults = PROFILE_DEFAULTS[args.profile]
    interval = args.interval if args.interval is not None else float(defaults["interval"])
    scene_threshold = args.scene_threshold if args.scene_threshold is not None else float(defaults["scene_threshold"])
    max_width = args.max_width if args.max_width is not None else int(defaults["max_width"])
    max_frames = args.max_frames if args.max_frames is not None else int(defaults["max_frames"])
    if interval <= 0 or max_frames <= 0 or max_width <= 0 or args.dedupe_hamming < 0:
        parser.error("interval, max-frames, max-width, and dedupe-hamming must be non-negative/positive")

    job_dir = args.job.expanduser().resolve()
    if is_audio_only_job(read_json(job_dir / "metadata" / "job.json")):
        parser.error("Audio-only evidence mode does not permit keyframe extraction")
    video = args.video.expanduser().resolve()
    if not video.is_file():
        parser.error(f"Video does not exist: {video}")

    duration = probe_duration(video)
    start_seconds = parse_time(args.start) or 0.0
    end_seconds = parse_time(args.end) if args.end is not None else duration
    if start_seconds < 0 or end_seconds <= start_seconds or start_seconds >= duration:
        parser.error("The focus range must be inside the video and end after start")
    end_seconds = min(end_seconds, duration)
    effective_duration = max(0.5, end_seconds - start_seconds)
    sampling_interval = min(interval, max(0.5, effective_duration / 2.0))
    cue_timestamps = parse_timestamps(args.timestamps)
    out_of_scope = [point for point in cue_timestamps if not start_seconds <= point <= end_seconds]
    cue_timestamps = [point for point in cue_timestamps if start_seconds <= point <= end_seconds]

    candidates_dir = job_dir / "frames" / "candidates"
    selected_dir = job_dir / "frames" / "selected"
    interval_dir = candidates_dir / ".interval_tmp"
    scene_dir = candidates_dir / ".scene_tmp"
    iframe_dir = candidates_dir / ".iframe_tmp"
    cue_dir = candidates_dir / ".cue_tmp"
    for temp_dir in (interval_dir, scene_dir, iframe_dir, cue_dir):
        if temp_dir.exists():
            shutil.rmtree(temp_dir)

    scale = f"scale=w='min({max_width},iw)':h=-2"
    rows: list[dict[str, Any]] = []
    if args.profile == "quick":
        timestamps = extract_iframe_candidates(
            video, iframe_dir, max_width, start_seconds, end_seconds
        )
        if len(timestamps) < 4:
            for image in iframe_dir.glob("frame_*.jpg"):
                image.unlink()
            timestamps = extract_frames(
                video,
                iframe_dir,
                f"fps=1/{sampling_interval},showinfo,{scale}",
                start_seconds,
                end_seconds,
            )
            sources = (("interval", iframe_dir, timestamps),)
        else:
            sources = (("iframe", iframe_dir, timestamps),)
    else:
        interval_times = extract_frames(
            video,
            interval_dir,
            f"fps=1/{sampling_interval},showinfo,{scale}",
            start_seconds,
            end_seconds,
        )
        scene_times = extract_frames(
            video,
            scene_dir,
            f"select='gt(scene,{scene_threshold})',showinfo,{scale}",
            start_seconds,
            end_seconds,
        )
        sources = (("interval", interval_dir, interval_times), ("scene", scene_dir, scene_times))

    for source, folder, timestamps in sources:
        files = sorted(folder.glob("frame_*.jpg"))
        if len(files) != len(timestamps):
            if source == "interval":
                timestamps = [round(start_seconds + index * sampling_interval, 3) for index in range(len(files))]
            elif source == "iframe":
                span = max(0.0, end_seconds - start_seconds)
                step = span / max(1, len(files) - 1)
                timestamps = [round(start_seconds + index * step, 3) for index in range(len(files))]
            else:
                raise RuntimeError(f"Could not align {len(files)} {source} frames with {len(timestamps)} timestamps")
        for image, timestamp in zip(files, timestamps, strict=True):
            target = candidates_dir / f"{source}_t{timestamp:010.3f}.jpg"
            image.replace(target)
            rows.append(
                {
                    "source": source,
                    "timestamp": round(timestamp, 3),
                    "candidate_path": str(target.relative_to(job_dir)),
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    "pinned": False,
                }
            )
        shutil.rmtree(folder)

    # Explicit endpoints protect short/static clips where an fps filter may emit
    # only the first frame. They also make the reported temporal gap meaningful.
    endpoint_times = [start_seconds]
    if end_seconds - start_seconds > 0.05:
        tail_offset = min(0.5, max(0.05, effective_duration / 4.0))
        endpoint_times.append(max(start_seconds, end_seconds - tail_offset))
    known_times = [float(row["timestamp"]) for row in rows]
    for endpoint in endpoint_times:
        if any(abs(endpoint - known) <= 0.5 for known in known_times):
            continue
        endpoint_path = candidates_dir / f"interval_t{endpoint:010.3f}.jpg"
        if extract_one_frame(video, endpoint_path, endpoint, max_width):
            rows.append(
                {
                    "source": "interval",
                    "timestamp": round(endpoint, 3),
                    "candidate_path": str(endpoint_path.relative_to(job_dir)),
                    "sha256": hashlib.sha256(endpoint_path.read_bytes()).hexdigest(),
                    "pinned": False,
                    "reason": "scope-endpoint",
                }
            )
            known_times.append(endpoint)

    for timestamp in cue_timestamps:
        cue_path = cue_dir / f"cue_t{timestamp:010.3f}.jpg"
        if not extract_one_frame(video, cue_path, timestamp, max_width):
            continue
        target = candidates_dir / cue_path.name
        cue_path.replace(target)
        rows.append(
            {
                "source": "cue",
                "timestamp": timestamp,
                "candidate_path": str(target.relative_to(job_dir)),
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "pinned": True,
                "reason": "transcript-cue",
            }
        )
    if cue_dir.exists():
        shutil.rmtree(cue_dir)

    deduplicated, dedupe_dropped = deduplicate_rows(
        rows, sampling_interval, args.dedupe_hamming, job_dir
    )
    selected, pinned_over_budget = select_with_pins(deduplicated, max_frames)

    prior = existing_manifest(job_dir) if args.append else {}
    existing_rows = list(prior.get("frames", []))
    selected_dir.mkdir(parents=True, exist_ok=True)
    if not args.append:
        for existing in selected_dir.glob("F*.jpg"):
            existing.unlink()

    existing_timestamps = {round(float(row.get("timestamp", -1)), 3) for row in existing_rows}
    next_number = next_frame_number(existing_rows)
    new_rows: list[dict[str, Any]] = []
    for row in selected:
        if args.append and round(float(row["timestamp"]), 3) in existing_timestamps:
            continue
        frame_id = f"F{next_number:04d}" if args.append else f"F{len(new_rows) + 1:04d}"
        if args.append:
            next_number += 1
        source = job_dir / row["candidate_path"]
        target = selected_dir / f"{frame_id}_t{row['timestamp']:010.3f}.jpg"
        shutil.copy2(source, target)
        row["frame_id"] = frame_id
        row["selected_path"] = str(target.relative_to(job_dir))
        new_rows.append(row)

    all_rows = sorted(existing_rows + new_rows, key=lambda row: float(row["timestamp"]))
    run_record = {
        "profile": args.profile,
        "scope_start": round(start_seconds, 3),
        "scope_end": round(end_seconds, 3),
        "requested_cue_timestamps": cue_timestamps,
        "dropped_out_of_scope_cues": out_of_scope,
        "candidate_count": len(rows),
        "deduplicated_count": len(deduplicated),
        "dedupe_dropped": dedupe_dropped,
        "selected_count": len(new_rows),
        "pinned_count": sum(1 for row in new_rows if row.get("source") == "cue"),
        "pinned_over_budget": pinned_over_budget,
        "scope_max_temporal_gap_seconds": max_temporal_gap(
            new_rows, start_seconds, end_seconds
        ),
    }
    runs = list(prior.get("runs", [])) if args.append else []
    runs.append(run_record)
    manifest = {
        "schema_version": "1.1",
        "video": str(video),
        "duration_seconds": round(duration, 3),
        "settings": {
            "profile": args.profile,
            "interval_seconds": interval,
            "effective_interval_seconds": sampling_interval,
            "scene_threshold": scene_threshold,
            "max_frames": max_frames,
            "max_width": max_width,
            "dedupe_hamming": args.dedupe_hamming,
            "dedupe_method": "batch_average_hash",
        },
        "scope": {"start_seconds": round(start_seconds, 3), "end_seconds": round(end_seconds, 3)},
        "candidate_count": int(prior.get("candidate_count", 0)) + len(rows),
        "deduplicated_count": int(prior.get("deduplicated_count", 0)) + len(deduplicated),
        "selected_count": len(all_rows),
        "max_temporal_gap_seconds": max_temporal_gap(all_rows, 0.0, duration),
        "pinned_count": sum(1 for row in all_rows if row.get("source") == "cue"),
        "runs": runs,
        "frames": all_rows,
    }
    cloud_manifest = {
        "schema_version": "1.1",
        "policy": "selected_frames_only",
        "contains_authentication_secrets": False,
        "scan_profile": args.profile,
        "frames": [
            {
                "frame_id": row["frame_id"],
                "timestamp": row["timestamp"],
                "path": row["selected_path"],
                "source": row.get("source"),
                "pinned": bool(row.get("pinned")),
            }
            for row in all_rows
        ],
    }
    write_json(job_dir / "frames" / "frame_manifest.json", manifest)
    write_json(job_dir / "frames" / "cloud_manifest.json", cloud_manifest)
    update_pipeline_state(
        job_dir,
        "keyframes",
        "complete",
        f"{len(new_rows)} selected frames in {args.profile} profile; {len(out_of_scope)} cue timestamps out of scope",
    )
    print(job_dir / "frames" / "cloud_manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
