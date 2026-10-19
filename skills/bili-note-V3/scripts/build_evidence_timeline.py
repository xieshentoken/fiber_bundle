#!/usr/bin/env python3
"""Fuse timestamped transcript cues and cloud frame analyses into evidence segments."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from common import (
    Cue,
    EVIDENCE_MODE_AUDIO_ONLY,
    evidence_mode,
    is_audio_only_job,
    parse_srt,
    read_json,
    read_jsonl,
    update_pipeline_state,
    utc_now,
    write_json,
    write_jsonl,
)
from audit_visual_evidence import validate_visual_rows


# A frame is only bound to a transcript segment when it lands within this window
# of the segment.  Frames farther away become their own visual-only segment.
FRAME_ATTACH_WINDOW_SECONDS = 2.0


def find_transcript(job_dir: Path) -> Path:
    candidates = (
        job_dir / "transcript" / "subtitle_normalized.srt",
        job_dir / "transcript" / "asr_normalized.srt",
    )
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError("No normalized SRT transcript was found")


def _scene_break_between(
    scene_boundaries: list[float], previous_end: float, next_start: float
) -> bool:
    """Return True when a scene change falls between two adjacent cues.

    Splitting on a scene change keeps an evidence segment aligned with a single
    on-screen visual (one slide / one board), which is what lets a frame bind to
    the sentence that actually describes it.
    """
    low = min(previous_end, next_start)
    high = max(previous_end, next_start)
    return any(low <= boundary <= high for boundary in scene_boundaries)


def group_cues(
    cues: list[Cue],
    max_duration: float,
    max_gap: float,
    scene_boundaries: list[float] | None = None,
) -> list[dict[str, Any]]:
    boundaries = sorted(scene_boundaries or [])
    segments: list[dict[str, Any]] = []
    current: list[Cue] = []
    for cue in cues:
        if cue.start is None or cue.end is None:
            continue
        if current:
            current_start = current[0].start or 0.0
            previous_end = current[-1].end or current_start
            gap = cue.start - previous_end
            projected_duration = cue.end - current_start
            scene_break = _scene_break_between(boundaries, previous_end, cue.start)
            if gap > max_gap or projected_duration > max_duration or scene_break:
                segments.append(cues_to_segment(current))
                current = []
        current.append(cue)
    if current:
        segments.append(cues_to_segment(current))
    return segments


def cues_to_segment(cues: list[Cue]) -> dict[str, Any]:
    return {
        "start": cues[0].start,
        "end": cues[-1].end,
        "transcript": " ".join(cue.text.replace("\n", " ") for cue in cues),
        "cue_indices": [cue.index for cue in cues],
        "frames": [],
    }


def _segment_distance(segment: dict[str, Any], timestamp: float) -> float:
    """Return how far a timestamp sits from a segment interval (0 when inside)."""
    start = float(segment["start"])
    end = float(segment["end"])
    if timestamp < start:
        return start - timestamp
    if timestamp > end:
        return timestamp - end
    return 0.0


def nearest_segment_index(segments: list[dict[str, Any]], timestamp: float) -> int | None:
    """Pick the single closest segment; ties break toward the earlier segment.

    Binding a frame to exactly one segment (instead of every segment whose
    padded window overlaps it) is what stops one screenshot from being attached
    to several unrelated claims.
    """
    best_index: int | None = None
    best_distance = float("inf")
    for index, segment in enumerate(segments):
        distance = _segment_distance(segment, timestamp)
        if distance < best_distance:
            best_distance = distance
            best_index = index
            if distance == 0.0:
                break
    return best_index


def representative_cue(
    segment: dict[str, Any], cue_by_index: dict[int, Cue], timestamp: float
) -> Cue | None:
    """Return the cue inside a segment whose time is closest to the frame."""
    best_cue: Cue | None = None
    best_distance = float("inf")
    for cue_index in segment.get("cue_indices", []):
        cue = cue_by_index.get(int(cue_index))
        if cue is None or cue.start is None:
            continue
        center = (cue.start + (cue.end if cue.end is not None else cue.start)) / 2.0
        distance = abs(center - timestamp)
        if distance < best_distance:
            best_distance = distance
            best_cue = cue
    return best_cue


def cue_coverage(cues: list[Cue], segments: list[dict[str, Any]]) -> tuple[float, list[int]]:
    cue_ids = {cue.index for cue in cues}
    covered_ids = {
        int(cue_index)
        for segment in segments
        for cue_index in segment.get("cue_indices", [])
    }
    missing_ids = sorted(cue_ids - covered_ids)
    ratio = len(covered_ids & cue_ids) / len(cue_ids) if cue_ids else 1.0
    return ratio, missing_ids


def source_coverage_fields(job_dir: Path) -> dict[str, Any]:
    path = job_dir / "metadata" / "source_coverage.json"
    if not path.exists():
        return {}
    report = read_json(path)
    return {
        "source_coverage_ratio": report.get("coverage_ratio"),
        "source_declared_duration_seconds": report.get("declared_duration_seconds"),
        "source_acquired_duration_seconds": report.get("acquired_duration_seconds"),
        "source_coverage_complete": report.get("complete") is True,
        "source_state": report.get("state"),
    }


def write_audio_only_coverage(
    job_dir: Path,
    transcript_path: Path,
    cues: list[Cue],
    segments: list[dict[str, Any]],
    unresolved_terms: list[dict[str, Any]],
    asr_coverage: dict[str, Any] | None = None,
) -> None:
    transcript_coverage, missing_cues = cue_coverage(cues, segments)
    coverage = {
        "schema_version": "1.2",
        "evidence_mode": EVIDENCE_MODE_AUDIO_ONLY,
        "transcript_file": str(transcript_path.relative_to(job_dir)),
        "transcript_cues": len(cues),
        "covered_transcript_cues": len(cues) - len(missing_cues),
        "transcript_coverage_ratio": round(transcript_coverage, 4),
        "missing_cue_indices": missing_cues,
        "evidence_segments": len(segments),
        "selected_frames": 0,
        "covered_frames": 0,
        "visual_coverage_ratio": None,
        "visual_coverage_status": "not_applicable",
        "unresolved_term_candidates": len(unresolved_terms),
        "illustration_candidates": 0,
        "illustrations_created": 0,
        "illustrations_failed": 0,
        "danmaku_used": False,
        "comments_used": False,
        "live_chat_used": False,
    }
    coverage.update(source_coverage_fields(job_dir))
    if asr_coverage:
        coverage["asr_coverage_ratio"] = asr_coverage.get("coverage_ratio")
        coverage["asr_gap_ranges"] = asr_coverage.get("gap_ranges", [])
        coverage["asr_failed_chunk_count"] = asr_coverage.get("failed_chunk_count", 0)
    write_jsonl(job_dir / "evidence" / "segments.jsonl", segments)
    write_json(job_dir / "evidence" / "coverage.json", coverage)
    lines = [
        "# Evidence coverage",
        "",
        "- Evidence mode: audio-only",
        f"- Transcript cues: {coverage['covered_transcript_cues']}/{coverage['transcript_cues']} ({transcript_coverage:.1%})",
        f"- Evidence segments: {coverage['evidence_segments']}",
        "- Visual coverage: not applicable (audio-only evidence)",
        "- Cropped illustrations: not applicable (audio-only evidence)",
        f"- Unresolved terminology candidates: {coverage['unresolved_term_candidates']}",
        "- Danmaku used: no",
    ]
    if missing_cues:
        lines.extend(["", "## Transcript cues still requiring review", "", ", ".join(map(str, missing_cues))])
    (job_dir / "evidence" / "coverage.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    update_pipeline_state(job_dir, "keyframes", "skipped", "Audio-only evidence mode")
    update_pipeline_state(job_dir, "visual_analysis", "skipped", "Audio-only evidence mode")
    update_pipeline_state(job_dir, "illustrations", "skipped", "Audio-only evidence mode")
    evidence_status = "complete" if not missing_cues and not unresolved_terms else "needs_review"
    update_pipeline_state(job_dir, "evidence", evidence_status, f"{len(segments)} audio-only segments")


def _ocr_keywords(visual_row: dict[str, Any] | None, limit: int = 12) -> list[str]:
    if not isinstance(visual_row, dict):
        return []
    ocr = visual_row.get("ocr_text")
    keywords: list[str] = []
    if isinstance(ocr, list):
        for entry in ocr:
            text = str(entry).strip()
            if text:
                keywords.append(text)
    elif isinstance(ocr, str) and ocr.strip():
        keywords.append(ocr.strip())
    return keywords[:limit]


def write_placement_map(
    job_dir: Path,
    segments: list[dict[str, Any]],
    illustration_manifest: dict[str, Any],
    visual_by_id: dict[str, dict[str, Any]],
) -> Path:
    """Emit a deterministic map telling the writer where each crop belongs.

    Every created illustration is tied to the single segment its frame was bound
    to, plus the representative transcript sentence and OCR keywords for that
    frame.  The note writer places images by looking this up instead of guessing
    from timestamps, and the audit checks bindings against it (A + B).
    """
    frame_to_segment: dict[str, dict[str, Any]] = {}
    for segment in segments:
        for frame in segment.get("frames", []):
            frame_id = str(frame.get("frame_id") or "")
            if frame_id and frame_id not in frame_to_segment:
                frame_to_segment[frame_id] = {"segment": segment, "frame": frame}

    entries: list[dict[str, Any]] = []
    for item in illustration_manifest.get("items", []):
        if item.get("status") != "created":
            continue
        frame_id = str(item.get("frame_id") or "")
        located = frame_to_segment.get(frame_id)
        segment = located["segment"] if located else None
        frame = located["frame"] if located else {}
        entries.append(
            {
                "illustration_id": str(item.get("illustration_id") or ""),
                "frame_id": frame_id,
                "timestamp": item.get("timestamp"),
                "caption": str(item.get("caption") or "").strip(),
                "kind": item.get("kind"),
                "path": item.get("path"),
                "primary_segment_id": segment.get("segment_id") if segment else None,
                "representative_cue_index": frame.get("representative_cue_index"),
                "representative_text": frame.get("representative_text", ""),
                "offset_seconds": frame.get("offset_seconds"),
                "segment_transcript": segment.get("transcript", "") if segment else "",
                "ocr_keywords": _ocr_keywords(visual_by_id.get(frame_id)),
            }
        )

    placement = {
        "schema_version": "1.0",
        "generated_at": utc_now(),
        "policy": "one_frame_one_segment",
        "attach_window_seconds": FRAME_ATTACH_WINDOW_SECONDS,
        "items": entries,
    }
    path = job_dir / "evidence" / "illustration_placement.json"
    write_json(path, placement)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument(
        "--visual-results",
        type=Path,
        help="JSONL created from cloud analysis; defaults to visual/visual_results.jsonl",
    )
    parser.add_argument("--max-segment", type=float, default=30.0)
    parser.add_argument("--max-gap", type=float, default=5.0)
    args = parser.parse_args()

    job_dir = args.job.expanduser().resolve()
    job = read_json(job_dir / "metadata" / "job.json")
    audio_only = is_audio_only_job(job)
    transcript_path = find_transcript(job_dir)
    cues = parse_srt(transcript_path.read_text(encoding="utf-8-sig"))
    if not cues:
        parser.error(f"No timestamped cues found in {transcript_path}")
    cue_by_index = {int(cue.index): cue for cue in cues}

    corrections_path = job_dir / "transcript" / "term_corrections.jsonl"
    corrections = list(read_jsonl(corrections_path)) if corrections_path.exists() else []
    unresolved_terms = [row for row in corrections if not row.get("applied")]
    asr_coverage_path = job_dir / "transcript" / "asr_coverage.json"
    asr_coverage = (
        read_json(asr_coverage_path)
        if transcript_path.name.startswith("asr_") and asr_coverage_path.exists()
        else {}
    )
    if audio_only:
        segments = group_cues(cues, args.max_segment, args.max_gap)
        write_audio_only_coverage(
            job_dir, transcript_path, cues, segments, unresolved_terms, asr_coverage
        )
        print(job_dir / "evidence" / "segments.jsonl")
        return 0

    frame_manifest_path = job_dir / "frames" / "frame_manifest.json"
    frame_manifest = read_json(frame_manifest_path) if frame_manifest_path.exists() else {"frames": []}
    selected_frames = frame_manifest.get("frames", [])
    # Break segments at scene-change frames so one segment stays aligned with a
    # single on-screen visual before frames are bound to it (C).
    scene_boundaries = [
        float(frame["timestamp"])
        for frame in selected_frames
        if str(frame.get("source")) == "scene" and frame.get("timestamp") is not None
    ]
    segments = group_cues(cues, args.max_segment, args.max_gap, scene_boundaries)
    visual_path = args.visual_results or (job_dir / "visual" / "visual_results.jsonl")
    visual_rows = list(read_jsonl(visual_path)) if visual_path.exists() else []
    validated_frames, visual_rows, visual_errors = validate_visual_rows(
        job_dir,
        visual_rows=visual_rows,
    )
    if visual_errors:
        raise ValueError("Visual evidence audit failed: " + "; ".join(visual_errors))
    # Use the normalized manifest rows returned by the validator so every
    # downstream timestamp comes from frame_manifest.json, never from a model
    # or an ad-hoc visual-results writer.
    selected_frames = list(validated_frames.values())
    visual_by_id = {row["frame_id"]: row for row in visual_rows}
    illustration_manifest_path = job_dir / "illustrations" / "manifest.json"
    illustration_manifest = (
        read_json(illustration_manifest_path)
        if illustration_manifest_path.exists()
        else {"candidate_count": 0, "created_count": 0, "failed_count": 0, "items": []}
    )
    illustrations_by_frame: dict[str, list[dict[str, Any]]] = {}
    for item in illustration_manifest.get("items", []):
        if item.get("status") == "created":
            illustrations_by_frame.setdefault(str(item.get("frame_id")), []).append(item)

    # Bind each frame to exactly one segment: its nearest transcript segment
    # when the frame lands inside the attach window, otherwise a dedicated
    # visual-only segment.  This is the core fix for image/text mismatch (A).
    def frame_attachment(frame: dict[str, Any], segment: dict[str, Any] | None) -> dict[str, Any]:
        frame_id = frame["frame_id"]
        timestamp = float(frame["timestamp"])
        attachment: dict[str, Any] = {
            "frame_id": frame_id,
            "timestamp": timestamp,
            "path": frame.get("selected_path"),
            "analysis": visual_by_id.get(frame_id),
            "illustrations": illustrations_by_frame.get(frame_id, []),
        }
        if segment is not None:
            cue = representative_cue(segment, cue_by_index, timestamp)
            if cue is not None:
                attachment["representative_cue_index"] = int(cue.index)
                attachment["representative_text"] = cue.text.replace("\n", " ").strip()
                if cue.start is not None:
                    attachment["offset_seconds"] = round(timestamp - float(cue.start), 3)
        return attachment

    for frame in selected_frames:
        timestamp = float(frame["timestamp"])
        index = nearest_segment_index(segments, timestamp)
        if index is not None and _segment_distance(segments[index], timestamp) <= FRAME_ATTACH_WINDOW_SECONDS:
            segment = segments[index]
            segment["frames"].append(frame_attachment(frame, segment))
        else:
            segments.append(
                {
                    "start": timestamp,
                    "end": timestamp,
                    "transcript": "",
                    "cue_indices": [],
                    "frames": [frame_attachment(frame, None)],
                    "visual_only": True,
                }
            )

    segments.sort(key=lambda segment: (float(segment["start"]), float(segment["end"])))
    for index, segment in enumerate(segments, start=1):
        segment["segment_id"] = f"S{index:04d}"

    write_placement_map(
        job_dir,
        segments,
        illustration_manifest,
        visual_by_id,
    )

    analyzed_ids = {
        row["frame_id"]
        for row in visual_rows
        if row["analysis_status"] in {"analyzed", "not_needed"}
    }
    selected_ids = {frame["frame_id"] for frame in selected_frames}
    missing_ids = sorted(selected_ids - analyzed_ids)
    visual_coverage = len(analyzed_ids & selected_ids) / len(selected_ids) if selected_ids else 1.0
    coverage = {
        "schema_version": "1.2",
        "evidence_mode": evidence_mode(job),
        "transcript_file": str(transcript_path.relative_to(job_dir)),
        "transcript_cues": len(cues),
        "covered_transcript_cues": len(cues),
        "transcript_coverage_ratio": 1.0,
        "missing_cue_indices": [],
        "evidence_segments": len(segments),
        "selected_frames": len(selected_ids),
        "covered_frames": len(analyzed_ids & selected_ids),
        "visual_coverage_ratio": round(visual_coverage, 4),
        "missing_frame_ids": missing_ids,
        "unresolved_term_candidates": len(unresolved_terms),
        "illustration_candidates": int(illustration_manifest.get("candidate_count") or 0),
        "illustrations_created": int(illustration_manifest.get("created_count") or 0),
        "illustrations_failed": int(illustration_manifest.get("failed_count") or 0),
        "danmaku_used": False,
        "comments_used": False,
        "live_chat_used": False,
    }
    coverage.update(source_coverage_fields(job_dir))
    if asr_coverage:
        coverage["asr_coverage_ratio"] = asr_coverage.get("coverage_ratio")
        coverage["asr_gap_ranges"] = asr_coverage.get("gap_ranges", [])
        coverage["asr_failed_chunk_count"] = asr_coverage.get("failed_chunk_count", 0)
    write_jsonl(job_dir / "evidence" / "segments.jsonl", segments)
    write_json(job_dir / "evidence" / "coverage.json", coverage)
    lines = [
        "# Evidence coverage",
        "",
        f"- Transcript cues: {coverage['transcript_cues']}",
        f"- Evidence segments: {coverage['evidence_segments']}",
        f"- Visual coverage: {coverage['covered_frames']}/{coverage['selected_frames']} ({visual_coverage:.1%})",
        f"- Unresolved terminology candidates: {coverage['unresolved_term_candidates']}",
        f"- Cropped illustrations: {coverage['illustrations_created']}/{coverage['illustration_candidates']}",
        "- Danmaku used: no",
    ]
    if missing_ids:
        lines.extend(["", "## Frames still requiring review", "", ", ".join(missing_ids)])
    (job_dir / "evidence" / "coverage.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    update_pipeline_state(
        job_dir,
        "visual_analysis",
        "complete" if not missing_ids else "needs_review",
        f"{len(analyzed_ids & selected_ids)}/{len(selected_ids)} selected frames covered",
    )
    evidence_status = "complete" if not missing_ids and not unresolved_terms else "needs_review"
    update_pipeline_state(
        job_dir,
        "evidence",
        evidence_status,
        f"{len(segments)} segments",
    )
    print(job_dir / "evidence" / "segments.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
