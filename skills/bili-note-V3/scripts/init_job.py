#!/usr/bin/env python3
"""Create a safe, reproducible Bili Note V3 job material package."""

from __future__ import annotations

import argparse
from pathlib import Path

from common import (
    EVIDENCE_MODE_AUDIO_ONLY,
    EVIDENCE_MODE_FULL,
    utc_now,
    validate_video_source,
    write_json,
)


JOB_DIRS = (
    "metadata",
    "source",
    "terms",
    "transcript",
    "frames/candidates",
    "frames/selected",
    "visual",
    "illustrations",
    "evidence",
    "review",
    "note",
    "output",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="Bilibili or YouTube single-video URL/id")
    parser.add_argument("--out", required=True, type=Path, help="New job directory")
    parser.add_argument("--topic", default="", help="User-provided domain context")
    parser.add_argument("--title", default="", help="Known video title")
    parser.add_argument(
        "--evidence-mode",
        choices=("full", "audio-only"),
        default="full",
        help="Use audio-only evidence for conversational videos; skips frame extraction and vision analysis",
    )
    parser.add_argument(
        "--cloud-vision",
        choices=("allowed", "disabled"),
        default="allowed",
        help="Whether selected frames may be sent to a cloud vision model",
    )
    parser.add_argument(
        "--keep-media",
        action="store_true",
        help="Retain downloaded source media after a successful published-note audit",
    )
    args = parser.parse_args()

    audio_only = args.evidence_mode == "audio-only"
    selected_evidence_mode = EVIDENCE_MODE_AUDIO_ONLY if audio_only else EVIDENCE_MODE_FULL

    platform_name, source_id, canonical_url = validate_video_source(args.source)
    job_dir = args.out.expanduser().resolve()
    if job_dir.exists() and any(job_dir.iterdir()):
        parser.error(f"Refusing to initialize a non-empty directory: {job_dir}")
    for relative in JOB_DIRS:
        (job_dir / relative).mkdir(parents=True, exist_ok=True)

    created_at = utc_now()
    job = {
        "schema_version": "3.0",
        "created_at": created_at,
        "source": {
            "platform": platform_name,
            "url": canonical_url,
            "id": source_id,
            "title": args.title,
        },
        "topic": args.topic,
        "policies": {
            "danmaku": "forbidden",
            "comments": "forbidden",
            "live_chat": "forbidden",
            "authentication": "browser_session_only",
            "cookie_reading": "forbidden",
            "evidence_mode": selected_evidence_mode,
            "cloud_vision": "disabled" if audio_only else args.cloud_vision,
            "cloud_upload_scope": "none" if audio_only else "selected_frames_only",
            "note_illustrations": "none" if audio_only else "cropped_regions_only",
            "visual_timestamp_source": "frames/frame_manifest.json" if not audio_only else "not_applicable",
            "visual_verification": "not_applicable" if audio_only else "required",
            "semantic_binding_audit": "not_applicable" if audio_only else "required",
            "cleanup_source_media": "retain" if args.keep_media else "after_publish",
        },
    }
    if platform_name == "bilibili":
        job["source"]["bvid"] = source_id
    access = {
        "schema_version": "2.0",
        "status": "unknown",
        "access_state": "unknown",
        "source_state": "not_acquired",
        "handoff_state": "not_required",
        "browser_backend": None,
        "verified_at": None,
        "verified_by": None,
        "entitlement": None,
        "verification": {},
        "notes": "Do not store cookies, authorization headers, passwords, or signed media URLs here.",
        "secrets_stored": False,
    }
    glossary = {
        "schema_version": "1.0",
        "topic": args.topic,
        "confirmed": False,
        "confirmed_at": None,
        "confirmed_by": None,
        "confirmed_hash": None,
        "terms": [],
    }
    pipeline = {
        "schema_version": "1.0",
        "updated_at": created_at,
        "phases": {
            "initialize": {"status": "complete", "detail": source_id, "updated_at": created_at},
            "access": {"status": "blocked", "detail": "Browser access not verified", "updated_at": created_at},
            "terminology": {"status": "blocked", "detail": "Glossary not confirmed", "updated_at": created_at},
            "transcript": {"status": "pending", "detail": "", "updated_at": created_at},
            "keyframes": {
                "status": "skipped" if audio_only else "pending",
                "detail": "Audio-only evidence mode" if audio_only else "",
                "updated_at": created_at,
            },
            "visual_analysis": {
                "status": "skipped" if audio_only else "pending",
                "detail": "Audio-only evidence mode" if audio_only else "",
                "updated_at": created_at,
            },
            "illustrations": {
                "status": "skipped" if audio_only else "pending",
                "detail": "Audio-only evidence mode" if audio_only else "",
                "updated_at": created_at,
            },
            "evidence": {"status": "pending", "detail": "", "updated_at": created_at},
            "note": {"status": "pending", "detail": "", "updated_at": created_at},
            "publish": {"status": "pending", "detail": "", "updated_at": created_at},
            "cleanup": {"status": "pending", "detail": "", "updated_at": created_at},
        },
    }

    write_json(job_dir / "metadata" / "job.json", job)
    write_json(job_dir / "metadata" / "access.json", access)
    write_json(job_dir / "metadata" / "pipeline_state.json", pipeline)
    write_json(
        job_dir / "metadata" / "media_manifest.json",
        {"schema_version": "1.0", "created_at": created_at, "files": []},
    )
    write_json(job_dir / "terms" / "glossary.json", glossary)
    (job_dir / "terms" / "term_review.md").write_text(
        "# Terminology review\n\n"
        "Add domain terms with manage_glossary.py, then explicitly confirm the glossary "
        "before subtitle normalization or ASR. Confirm an empty glossary only after the user "
        "states that no special terminology is needed.\n",
        encoding="utf-8",
    )
    print(job_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
