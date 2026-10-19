#!/usr/bin/env python3
"""Classify whether a Bili Note V3 job may be reused or must resume safely.

This is intentionally deterministic and side-effect free.  A previous note is
not evidence that the source was complete: reuse requires a passed final audit,
complete declared-vs-local coverage, and a ready authenticated handoff when
the source is not public.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from common import (
    AUTHORIZED_ACCESS_STATES,
    canonical_access_state,
    is_audio_only_job,
    job_source_identity,
    read_json,
)


BROWSER_BACKENDS = ("edge", "chrome", "generic_browser")
PHASE_ORDER = (
    "access",
    "terminology",
    "transcript",
    "keyframes",
    "visual_analysis",
    "illustrations",
    "evidence",
    "note",
    "publish",
    "cleanup",
)
MEDIA_SUFFIXES = {
    ".aac",
    ".flac",
    ".m4a",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
}


def optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return read_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def source_media_present(job_dir: Path, manifest: dict[str, Any]) -> bool:
    if any(
        str(item.get("kind") or "").startswith("source_")
        for item in manifest.get("files", [])
        if isinstance(item, dict)
    ):
        return True
    source_dir = job_dir / "source"
    return any(
        path.is_file() and path.suffix.lower() in MEDIA_SUFFIXES
        for path in source_dir.iterdir()
    ) if source_dir.exists() else False


def earliest_incomplete_phase(pipeline: dict[str, Any], *, access_needs_work: bool) -> str:
    if access_needs_work:
        return "access"
    phases = pipeline.get("phases", {})
    for phase in PHASE_ORDER:
        status = str(phases.get(phase, {}).get("status") or "pending")
        if status not in {"complete", "skipped"}:
            return phase
    return "access"


def inspect_job(job_dir: Path) -> dict[str, Any]:
    metadata = job_dir / "metadata"
    job = optional_json(metadata / "job.json")
    access = optional_json(metadata / "access.json")
    pipeline = optional_json(metadata / "pipeline_state.json")
    final_audit = optional_json(metadata / "audit_final.json")
    visual_audit_path = metadata / "audit_visual.json"
    visual_audit = optional_json(visual_audit_path)
    coverage_path = metadata / "source_coverage.json"
    coverage = optional_json(coverage_path)
    manifest = optional_json(metadata / "media_manifest.json")

    schema_version = str(job.get("schema_version") or "legacy")
    legacy_job = not schema_version.startswith(("2.", "3."))
    try:
        platform_name, source_id = job_source_identity(job)
    except ValueError:
        platform_name, source_id = "", ""
    access_state = canonical_access_state(access)
    source_state = str(access.get("source_state") or "not_acquired")
    handoff_state = str(access.get("handoff_state") or "not_required")
    final_audit_passed = final_audit.get("passed") is True
    audio_only = is_audio_only_job(job) if job else False
    visual_audit_passed = audio_only or visual_audit.get("passed") is True
    source_evidence_present = source_media_present(job_dir, manifest)
    coverage_complete = coverage.get("complete") is True
    coverage_state = str(coverage.get("state") or "missing")
    authenticated_handoff_ready = (
        access_state == "public_verified"
        or (
            access_state in {"authenticated", "member_authorized"}
            and handoff_state == "ready"
        )
    )

    source_incomplete = source_evidence_present and not coverage_complete
    needs_browser = bool(
        access_state in {"auth_required"}
        or (
            access_state in {"authenticated", "member_authorized"}
            and handoff_state != "ready"
        )
        or source_state in {"preview_limited", "partial", "unverified"}
        or source_incomplete
    )

    if access_state in {"denied", "drm_blocked"}:
        decision = "blocked"
        reason = access_state
    elif (
        final_audit_passed
        and coverage_complete
        and authenticated_handoff_ready
        and visual_audit_passed
        and not legacy_job
    ):
        decision = "reusable"
        reason = "final_audit_and_source_coverage_passed"
    elif needs_browser:
        decision = "waiting_user_action"
        if source_state in {"preview_limited", "partial", "unverified"}:
            reason = source_state
        elif coverage_state in {"preview_limited", "unverified"}:
            reason = coverage_state
        elif handoff_state in {"unavailable", "failed"}:
            reason = "handoff_unavailable"
        else:
            reason = "browser_handoff_required"
    elif not job:
        decision = "failed"
        reason = "job_metadata_missing"
    elif not source_evidence_present and access_state == "unknown":
        decision = "continue_public_probe"
        reason = "new_job_public_probe_allowed"
    else:
        decision = "resume_pipeline"
        reason = (
            "visual_evidence_audit_missing_or_failed"
            if not visual_audit_passed
            else "final_audit_not_passed"
        )

    access_needs_work = needs_browser or access_state not in AUTHORIZED_ACCESS_STATES
    resume_phase = earliest_incomplete_phase(
        pipeline, access_needs_work=access_needs_work
    )
    visual_or_later = PHASE_ORDER[PHASE_ORDER.index("visual_analysis") :]
    if (
        not visual_audit_passed
        and not access_needs_work
        and resume_phase in visual_or_later
    ):
        resume_phase = "visual_analysis"
    return {
        "schema_version": "1.0",
        "job_schema_version": schema_version,
        "legacy_job": legacy_job,
        "platform": platform_name,
        "source_id": source_id,
        "bvid": str(job.get("source", {}).get("bvid") or ""),
        "access_state": access_state,
        "source_state": source_state,
        "handoff_state": handoff_state,
        "browser_backend": access.get("browser_backend"),
        "source_evidence_present": source_evidence_present,
        "source_coverage_present": coverage_path.exists(),
        "source_coverage_complete": coverage_complete,
        "source_coverage_state": coverage_state,
        "final_audit_passed": final_audit_passed,
        "visual_audit_present": visual_audit_path.exists(),
        "visual_audit_passed": visual_audit_passed,
        "authenticated_handoff_ready": authenticated_handoff_ready,
        "decision": decision,
        "reusable": decision == "reusable",
        "resume_required": decision != "reusable",
        "resume_phase": resume_phase,
        "reason": reason,
        "browser_backends": list(BROWSER_BACKENDS),
        "secrets_stored": bool(access.get("secrets_stored") is True),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()
    report = inspect_job(args.job.expanduser().resolve())
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"decision={report['decision']}")
        print(f"reason={report['reason']}")
        print(f"resume_phase={report['resume_phase']}")
        print(f"reusable={report['reusable']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
