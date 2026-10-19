"""Visual-primary audit: common V3 safety gates plus explicit non-speech evidence gates."""

import json
import re
import sys
from pathlib import Path
from typing import Any

from .video_visual import DISCLOSURE, POLICY, checksum, owned, qualify, read, visual_rows, write


def audit(skill: Path, job: Path, *, final: bool) -> None:
    from audit_job import main as common_audit  # type: ignore[import-not-found]
    from audit_visual_evidence import build_report  # type: ignore[import-not-found]

    decision = qualify(skill, job)
    previous = sys.argv
    try:
        sys.argv = [str(skill / "scripts/audit_job.py"), str(job), "--stage", "ready-for-asr"]
        common = common_audit() == 0
    finally:
        sys.argv = previous
    frames, rows = visual_rows(job)
    visual = build_report(
        job, require_illustrations=True, require_bindings=True, require_note=final
    )
    write(job / "metadata/audit_visual.json", visual)
    coverage = read(job / "evidence/coverage.json")
    segments = [
        json.loads(line)
        for line in (job / "evidence/segments.jsonl").read_text().splitlines()
        if line
    ]
    linked = [f["frame_id"] for s in segments for f in s["frames"]]
    expected = [row["frame_id"] for row in rows if row["analysis_status"] == "analyzed"]
    by_id = {r["frame_id"]: r for r in rows}
    attachments = [f for s in segments for f in s["frames"]]
    crops = read(job / "illustrations/manifest.json")
    checks: dict[str, bool] = {
        "V3_access_glossary_privacy_and_full_source": common,
        "audited_all_empty_ASR": read(job / "metadata/fiber_visual_route.json") == decision,
        "visual_timestamps_provenance_crops_bindings": visual["passed"] is True,
        "every_analyzed_frame_bound_once": sorted(linked) == sorted(expected)
        and len(linked) == len(set(linked)),
        "frame_bytes_and_segment_evidence_unchanged": all(
            checksum(owned(job, f["selected_path"])) == f["sha256"] for f in frames.values()
        )
        and all(
            f["analysis"] == by_id[f["frame_id"]]
            and f["timestamp"] == frames[f["frame_id"]]["timestamp"]
            and f["path"] == frames[f["frame_id"]]["selected_path"]
            for f in attachments
        ),
        "no_fabricated_transcript": all(
            s["transcript"] == "" and s["cue_indices"] == [] and s["visual_only"] is True
            for s in segments
        ),
        "explicit_visual_coverage_scope": coverage.get("schema") == POLICY
        and coverage.get("coverage_scope") == "selected_frames_only"
        and coverage.get("selected_frames") == len(frames)
        and coverage.get("covered_frames") == len(rows)
        and coverage.get("visual_coverage_ratio") == 1.0,
        "speech_not_claimed_complete": coverage.get("speech_verified") is False
        and coverage.get("transcript_coverage_ratio") is None
        and coverage.get("transcript_cues") == 0,
        "no_failed_crops_or_unresolved_terms": coverage.get("illustrations_failed") == 0
        and crops.get("failed_count") == 0
        and coverage.get("illustrations_created") == crops.get("created_count")
        and coverage.get("unresolved_term_candidates") == 0,
        "no_comments_danmaku_or_chat": all(
            coverage.get(k) is False for k in ("danmaku_used", "comments_used", "live_chat_used")
        ),
    }
    if final:
        note = (job / "note/note.md").read_text()
        targets = re.findall(r"!\[[^\]]*\]\((<[^>]+>|[^\)]+)\)", note)
        crop_paths = {
            owned(job, x["path"])
            for x in read(job / "illustrations/manifest.json")["items"]
            if x.get("status") == "created"
        }
        references = []
        for target in targets:
            value = target.strip().strip("<>")
            references.append((job / "note" / value).resolve())
        checks.update(
            note_has_explicit_speech_limitation=DISCLOSURE in note,
            note_has_coverage_section="覆盖" in note,
            note_has_crops_or_discloses_none=bool(crop_paths) or "无适合裁剪的插图" in note,
            note_only_uses_registered_crops=set(references) == crop_paths
            and len(references) == len(crop_paths)
            and "<img" not in note.lower()
            and "![[" not in note,
            note_has_every_visual_segment=all(s["segment_id"] in note for s in segments),
        )
    report: dict[str, Any] = {
        "schema": "fiber-video-visual-primary-audit/v1",
        "stage": "final" if final else "ready-for-note",
        "evidence_mode": "visual_primary",
        "speech_verified": False,
        "passed": all(checks.values()),
        "checks": [{"name": k, "passed": v} for k, v in checks.items()],
    }
    write(
        job / ("metadata/audit_final.json" if final else "metadata/audit_visual_ready.json"), report
    )
    if not report["passed"]:
        raise ValueError("video_skill_visual_audit_failed")
