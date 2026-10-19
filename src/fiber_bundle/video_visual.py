"""Explicit visual-primary evidence contract; a missing transcript is never synthesized."""

import hashlib
import json
import math
import sys
import wave
from pathlib import Path
from typing import Any

POLICY = "fiber-video-visual-primary/v1"
DISCLOSURE = "画面主导整理：ASR未取得有效文字，未核验语音内容；笔记仅依据已分析的关键帧文字与图表。"


def checksum(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while raw := stream.read(1024 * 1024):
            value.update(raw)
    return value.hexdigest()


def read(path: Path) -> Any:
    return json.loads(path.read_bytes())


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)


def owned(job: Path, relative: str) -> Path:
    path = (job / relative).resolve()
    if not path.is_relative_to(job.resolve()) or (job / relative).is_symlink():
        raise ValueError("video_skill_visual_fallback_invalid")
    return path


def qualify(skill: Path, job: Path) -> dict[str, Any]:
    """Require a full, unchanged audio pass with empty results and zero processing failures."""
    diagnostic_path = job / "transcript/asr_diagnostic.json"
    report = read(diagnostic_path)
    audio = job / "transcript/asr_audio.wav"
    source = read(job / "metadata/source_coverage.json")
    media = owned(job, source["media_path"])
    with wave.open(str(audio)) as stream:
        duration = stream.getnframes() / stream.getframerate()
    chunks = report.get("chunks", [])
    cursor = 0.0
    if not chunks:
        raise ValueError("video_skill_visual_fallback_invalid")
    for index, row in enumerate(chunks, 1):
        start, end = row["start"], row["end"]
        if (
            row.get("chunk_index") != index
            or row.get("status") != "empty"
            or not all(type(v) in {int, float} and math.isfinite(v) for v in (start, end))
            or abs(start - cursor) > 0.01
            or end <= start
        ):
            raise ValueError("video_skill_visual_fallback_invalid")
        cursor = end
    expected = {
        "schema": "fiber-v3-asr-diagnostic/v1",
        "source_media_sha256": checksum(media),
        "audio_sha256": checksum(audio),
        "context_sha256": checksum(job / "transcript/asr_prompt.txt"),
        "backend_sha256": checksum(skill / "scripts/qwen3_asr_backend.py"),
        "transcript_segments": 0,
        "transcript_characters": 0,
    }
    if report.get("schema") == "fiber-api-asr-diagnostic/v1":
        expected.update(
            schema="fiber-api-asr-diagnostic/v1",
            backend_sha256=checksum(Path(__file__).with_name("video_asr_api.py")),
        )
        responses = report.get("response_files")
        if not isinstance(responses, dict) or len(responses) != len(chunks):
            raise ValueError("video_skill_visual_fallback_invalid")
        for rid, sha in responses.items():
            path = owned(job, "transcript/api-results/" + rid + ".json")
            reply = read(path)
            if (
                checksum(path) != sha
                or reply.get("result", {}).get("text") != ""
                or reply.get("policy_sha256") != report.get("policy_sha256")
            ):
                raise ValueError("video_skill_visual_fallback_invalid")
    if (
        any(report.get(k) != v for k, v in expected.items())
        or source.get("complete") is not True
        or duration <= 0
        or abs(cursor - duration) > 0.01
        or abs(duration - source["acquired_duration_seconds"]) > max(0.25, duration * 0.01)
        or any(
            (job / "transcript" / n).exists()
            for n in ("asr_normalized.srt", "subtitle_normalized.srt")
        )
    ):
        raise ValueError("video_skill_visual_fallback_invalid")
    return {
        "policy": POLICY,
        "reason": "all_asr_chunks_empty",
        "source_media_sha256": expected["source_media_sha256"],
        "diagnostic_sha256": checksum(diagnostic_path),
        "audio_sha256": expected["audio_sha256"],
        "speech_verified": False,
        "disclosure": DISCLOSURE,
    }


def visual_rows(job: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from audit_visual_evidence import validate_visual_rows  # type: ignore[import-not-found]

    frames, rows, errors = validate_visual_rows(job)
    if (
        errors
        or not frames
        or any(r["analysis_status"] not in {"analyzed", "not_needed"} for r in rows)
    ):
        raise ValueError("video_skill_visual_audit_failed")
    useful = False
    for row in rows:
        if row["analysis_status"] == "not_needed":
            if row.get("illustrations"):
                raise ValueError("video_skill_visual_audit_failed")
            continue
        for key in ("ocr_text", "claims"):
            items = row.get(key)
            if not isinstance(items, list) or any(not isinstance(x, str) for x in items):
                raise ValueError("video_skill_visual_content_missing")
            useful |= any(x.strip() for x in items)
    if not useful:
        raise ValueError("video_skill_visual_content_missing")
    return frames, rows


def illustration_binding(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "illustration_id": item["illustration_id"],
        "frame_id": item["frame_id"],
        "timestamp": item["timestamp"],
        "segment_id": item["primary_segment_id"],
        "caption": item["caption"].replace("\n", " "),
        "note_anchor": f"插图 {item['illustration_id']}（{item['timestamp']:.1f}s）",
        "verified": True,
    }


def frame_arguments(job: Path, media: Path) -> list[str]:
    """Pin the interval grid to exact source times; V3's FPS output otherwise shifts content."""
    duration = float(read(job / "metadata/source_coverage.json")["acquired_duration_seconds"])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("video_skill_source_incomplete")
    step = min(max(5.0, duration / 119), max(0.5, duration / 2))
    stamps = [round(index * step, 3) for index in range(min(120, math.ceil(duration / step)))]
    return [
        str(job),
        "--video",
        str(media),
        "--profile",
        "deep",
        "--interval",
        str(step),
        "--max-frames",
        "240",
        "--max-width",
        "960",
        "--timestamps",
        ",".join(map(str, stamps)),
    ]


def timeline(job: Path) -> None:
    from build_evidence_timeline import write_placement_map  # type: ignore[import-not-found]

    frames, rows = visual_rows(job)
    by_id = {row["frame_id"]: row for row in rows}
    illustrations = read(job / "illustrations/manifest.json")
    segments: list[dict[str, Any]] = []
    for frame in sorted(frames.values(), key=lambda row: row["timestamp"]):
        fid = frame["frame_id"]
        if by_id[fid]["analysis_status"] != "analyzed":
            continue
        segments.append(
            {
                "segment_id": f"V{len(segments) + 1:04d}",
                "start": frame["timestamp"],
                "end": frame["timestamp"],
                "transcript": "",
                "cue_indices": [],
                "visual_only": True,
                "frames": [
                    {
                        "frame_id": fid,
                        "timestamp": frame["timestamp"],
                        "path": frame["selected_path"],
                        "analysis": by_id[fid],
                        "illustrations": [
                            x
                            for x in illustrations["items"]
                            if x.get("frame_id") == fid and x.get("status") == "created"
                        ],
                    }
                ],
            }
        )
    path = job / "evidence/segments.jsonl"
    path.write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in segments) + "\n")
    write_placement_map(job, segments, illustrations, by_id)
    placement = read(job / "evidence/illustration_placement.json")["items"]
    bindings = [illustration_binding(item) for item in placement]
    (job / "evidence/illustration_bindings.jsonl").write_text(
        "\n".join(json.dumps(b, ensure_ascii=False) for b in bindings) + ("\n" if bindings else "")
    )
    corrections = job / "transcript/term_corrections.jsonl"
    unresolved = (
        [
            json.loads(line)
            for line in corrections.read_text().splitlines()
            if line and not json.loads(line).get("applied")
        ]
        if corrections.exists()
        else []
    )
    write(
        job / "evidence/coverage.json",
        {
            "schema": POLICY,
            "evidence_mode": "visual_primary",
            "transcript_file": None,
            "transcript_cues": 0,
            "transcript_coverage_ratio": None,
            "speech_verified": False,
            "evidence_segments": len(segments),
            "selected_frames": len(frames),
            "covered_frames": len(rows),
            "visual_coverage_ratio": 1.0,
            "coverage_scope": "selected_frames_only",
            "missing_frame_ids": [],
            "unresolved_term_candidates": len(unresolved),
            "illustrations_created": illustrations["created_count"],
            "illustrations_failed": illustrations["failed_count"],
            "danmaku_used": False,
            "comments_used": False,
            "live_chat_used": False,
            "source_coverage": read(job / "metadata/source_coverage.json"),
            "frame_sampling": {
                k: read(job / "frames/frame_manifest.json")[k]
                for k in ("settings", "selected_count", "max_temporal_gap_seconds")
            },
            "disclosure": DISCLOSURE,
        },
    )


def main() -> int:
    skill, job = (Path(arg).resolve() for arg in sys.argv[1:3])
    stage = sys.argv[3]
    try:
        decision = qualify(skill, job)
        path = job / "metadata/fiber_visual_route.json"
        if path.exists() and read(path) != decision:
            raise ValueError("video_skill_visual_fallback_invalid")
        if stage == "qualify":
            write(path, decision)
        elif not path.exists():
            raise ValueError("video_skill_visual_fallback_invalid")
        sys.path.insert(0, str(skill / "scripts"))
        if stage == "timeline":
            timeline(job)
        elif stage in {"ready-for-note", "final"}:
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from fiber_bundle.video_visual_audit import audit

            audit(skill, job, final=stage == "final")
        elif stage not in {"qualify", "verify"}:
            raise ValueError("video_skill_visual_fallback_invalid")
    except ValueError as error:
        return {
            "video_skill_visual_content_missing": 43,
            "video_skill_visual_audit_failed": 44,
        }.get(str(error), 42)
    except (OSError, KeyError, TypeError, wave.Error):
        return 42
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
