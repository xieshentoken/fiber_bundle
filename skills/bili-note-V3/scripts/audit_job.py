#!/usr/bin/env python3
"""Audit pipeline gates, privacy boundaries, coverage, and final note evidence."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from common import (
    AUDIO_ONLY_DISCLOSURE,
    AUTHORIZED_ACCESS_STATES,
    canonical_access_state,
    is_audio_only_job,
    read_json,
    sha256_json,
    write_json,
)
from audit_visual_evidence import build_report as build_visual_audit


AUTHORIZED = {"public", "authenticated", "member_authorized"}
SECRET_KEY_RE = re.compile(r"cookie|authorization|password|sessdata|access[_-]?token", re.I)
DANMAKU_RE = re.compile(r"danmaku|弹幕", re.I)
COMMENT_OR_CHAT_RE = re.compile(r"comments?|live[_-]?chat|评论|直播聊天", re.I)
OBSIDIAN_IMAGE_RE = re.compile(r"!\[\[[^\]]+\]\]")
HTML_IMAGE_RE = re.compile(r"<img\b[^>]*>", re.I)


def secret_keys(value: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            policy_value = isinstance(child, str) and child in {
                "forbidden",
                "browser_session_only",
                "selected_frames_only",
            }
            if SECRET_KEY_RE.search(str(key)) and not policy_value:
                found.append(path)
            found.extend(secret_keys(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(secret_keys(child, f"{prefix}[{index}]"))
    return found


def normalized_transcript(job_dir: Path) -> Path | None:
    for name in ("subtitle_normalized.srt", "asr_normalized.srt"):
        path = job_dir / "transcript" / name
        if path.exists():
            return path
    return None


def asr_coverage_path(job_dir: Path) -> Path:
    return job_dir / "transcript" / "asr_coverage.json"


def add_check(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def local_markdown_images(note_text: str) -> list[str]:
    targets = re.findall(r"!\[[^\]]*\]\((<[^>]+>|[^\)]+)\)", note_text)
    return [
        target.strip().strip("<>")
        for target in targets
        if not target.strip().strip("<>").startswith(("http://", "https://", "data:"))
    ]


def visual_artifact_paths(job_dir: Path) -> list[str]:
    roots = (job_dir / "frames", job_dir / "visual", job_dir / "illustrations")
    return [
        str(path.relative_to(job_dir))
        for root in roots
        if root.exists()
        for path in root.rglob("*")
        if path.is_file() and path.name not in {".DS_Store", "Thumbs.db"}
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument(
        "--stage",
        choices=("preflight", "ready-for-asr", "ready-for-cloud", "ready-for-note", "final"),
        default="preflight",
    )
    args = parser.parse_args()

    job_dir = args.job.expanduser().resolve()
    checks: list[dict[str, Any]] = []
    job_path = job_dir / "metadata" / "job.json"
    add_check(checks, "job_exists", job_path.exists(), str(job_path))
    if not job_path.exists():
        report = {"stage": args.stage, "passed": False, "checks": checks}
        print(report)
        return 1

    job = read_json(job_path)
    policies = job.get("policies", {})
    legacy_v2_job = str(job.get("schema_version") or "").startswith("2.")
    audio_only = is_audio_only_job(job)
    add_check(
        checks,
        "danmaku_forbidden",
        policies.get("danmaku") == "forbidden",
        str(policies.get("danmaku")),
    )
    danmaku_files = [
        str(path.relative_to(job_dir))
        for path in job_dir.rglob("*")
        if path.is_file() and DANMAKU_RE.search(path.name)
    ]
    add_check(checks, "no_danmaku_artifacts", not danmaku_files, ", ".join(danmaku_files))
    add_check(
        checks,
        "comments_and_live_chat_forbidden",
        policies.get("comments") in {"forbidden", "disabled_by_default"}
        and (policies.get("live_chat") == "forbidden" or legacy_v2_job),
        f"comments={policies.get('comments')}; live_chat={policies.get('live_chat')}",
    )
    interaction_files = [
        str(path.relative_to(job_dir))
        for path in job_dir.rglob("*")
        if path.is_file() and COMMENT_OR_CHAT_RE.search(path.name)
    ]
    add_check(
        checks,
        "no_comment_or_live_chat_artifacts",
        not interaction_files,
        ", ".join(interaction_files),
    )

    metadata_secret_keys: list[str] = []
    for path in (job_dir / "metadata").glob("*.json"):
        metadata_secret_keys.extend(f"{path.name}:{key}" for key in secret_keys(read_json(path)))
    add_check(
        checks,
        "no_authentication_secrets",
        not metadata_secret_keys,
        ", ".join(metadata_secret_keys),
    )

    access_path = job_dir / "metadata" / "access.json"
    access = read_json(access_path) if access_path.exists() else {}
    access_state = canonical_access_state(access)
    add_check(
        checks,
        "authorized_access",
        access_state in AUTHORIZED_ACCESS_STATES and access.get("secrets_stored") is not True,
        access_state,
    )
    if access_state in {"authenticated", "member_authorized"}:
        add_check(
            checks,
            "authenticated_handoff_ready",
            access.get("handoff_state") == "ready",
            str(access.get("handoff_state")),
        )

    glossary_path = job_dir / "terms" / "glossary.json"
    glossary = read_json(glossary_path) if glossary_path.exists() else {}
    glossary_confirmed = bool(glossary.get("confirmed")) and glossary.get(
        "confirmed_hash"
    ) == sha256_json(glossary.get("terms", []))
    add_check(checks, "glossary_confirmed", glossary_confirmed, str(glossary.get("confirmed_by")))

    if args.stage in {"ready-for-asr", "ready-for-cloud", "ready-for-note", "final"}:
        context_path = job_dir / "terms" / "asr_context.json"
        add_check(checks, "asr_context_exists", context_path.exists(), str(context_path))

        # Real acquired jobs have source metadata or registered source files;
        # synthetic evidence-only tests and glossary preflight jobs do not.  A
        # real job must prove declared-vs-local duration before any downstream
        # transcript, frame, or note gate can pass.
        source_metadata_path = job_dir / "metadata" / "source_metadata.json"
        source_coverage_path = job_dir / "metadata" / "source_coverage.json"
        media_manifest_path = job_dir / "metadata" / "media_manifest.json"
        media_manifest = read_json(media_manifest_path) if media_manifest_path.exists() else {}
        managed_source = any(
            str(item.get("kind") or "").startswith("source_")
            for item in media_manifest.get("files", [])
            if isinstance(item, dict)
        )
        source_gate_required = (
            source_metadata_path.exists() or source_coverage_path.exists() or managed_source
        )
        if source_gate_required:
            source_coverage = (
                read_json(source_coverage_path) if source_coverage_path.exists() else {}
            )
            add_check(
                checks,
                "source_coverage_complete",
                source_coverage_path.exists() and source_coverage.get("complete") is True,
                str(source_coverage or source_coverage_path),
            )

    transcript = normalized_transcript(job_dir)
    if args.stage in {"ready-for-cloud", "ready-for-note", "final"}:
        add_check(checks, "normalized_transcript", transcript is not None, str(transcript))
        if transcript is not None and transcript.name == "asr_normalized.srt":
            coverage_path_asr = asr_coverage_path(job_dir)
            asr_coverage = read_json(coverage_path_asr) if coverage_path_asr.exists() else {}
            add_check(
                checks,
                "asr_coverage_audited",
                coverage_path_asr.exists()
                and asr_coverage.get("failed_chunk_count") == 0
                and not asr_coverage.get("gap_ranges"),
                str(asr_coverage or coverage_path_asr),
            )

    frame_manifest_path = job_dir / "frames" / "frame_manifest.json"
    if args.stage in {"ready-for-cloud", "ready-for-note", "final"}:
        if audio_only:
            add_check(
                checks,
                "audio_only_skips_visual_pipeline",
                policies.get("cloud_vision") == "disabled"
                and policies.get("cloud_upload_scope") == "none",
                f"cloud_vision={policies.get('cloud_vision')}; scope={policies.get('cloud_upload_scope')}",
            )
            visual_artifacts = visual_artifact_paths(job_dir)
            add_check(
                checks,
                "audio_only_has_no_visual_artifacts",
                not visual_artifacts,
                ", ".join(visual_artifacts),
            )
        else:
            add_check(checks, "frame_manifest", frame_manifest_path.exists(), str(frame_manifest_path))
            cloud_allowed = policies.get("cloud_vision") == "allowed"
            add_check(checks, "cloud_vision_allowed", cloud_allowed, str(policies.get("cloud_vision")))
            add_check(
                checks,
                "visual_timestamp_source_declared",
                policies.get("visual_timestamp_source")
                in {None, "frames/frame_manifest.json"},
                str(policies.get("visual_timestamp_source")),
            )
            add_check(
                checks,
                "visual_verification_required",
                policies.get("visual_verification") in {None, "required"},
                str(policies.get("visual_verification")),
            )

    coverage_path = job_dir / "evidence" / "coverage.json"
    if args.stage in {"ready-for-note", "final"}:
        coverage = read_json(coverage_path) if coverage_path.exists() else {}
        add_check(checks, "evidence_coverage_exists", coverage_path.exists(), str(coverage_path))
        if not audio_only:
            add_check(
                checks,
                "visual_coverage_complete",
                coverage.get("visual_coverage_ratio") == 1.0,
                str(coverage.get("visual_coverage_ratio")),
            )
        add_check(
            checks,
            "terminology_resolved",
            coverage.get("unresolved_term_candidates") == 0,
            str(coverage.get("unresolved_term_candidates")),
        )
        add_check(
            checks,
            "evidence_excludes_danmaku",
            coverage.get("danmaku_used") is False,
            str(coverage.get("danmaku_used")),
        )
        add_check(
            checks,
            "evidence_excludes_comments_and_live_chat",
            (coverage.get("comments_used") is False or legacy_v2_job)
            and (coverage.get("live_chat_used") is False or legacy_v2_job),
            f"comments={coverage.get('comments_used')}; live_chat={coverage.get('live_chat_used')}",
        )
        if audio_only:
            add_check(
                checks,
                "audio_only_evidence_mode",
                coverage.get("evidence_mode") == "audio_only",
                str(coverage.get("evidence_mode")),
            )
            add_check(
                checks,
                "transcript_coverage_complete",
                coverage.get("transcript_coverage_ratio") == 1.0,
                str(coverage.get("transcript_coverage_ratio")),
            )
            add_check(
                checks,
                "audio_only_has_no_selected_frames",
                coverage.get("selected_frames") == 0,
                str(coverage.get("selected_frames")),
            )
            add_check(
                checks,
                "audio_only_has_no_illustrations",
                coverage.get("illustrations_created") == 0,
                str(coverage.get("illustrations_created")),
            )
        elif policies.get("note_illustrations") == "cropped_regions_only":
            illustration_manifest_path = job_dir / "illustrations" / "manifest.json"
            illustration_manifest = (
                read_json(illustration_manifest_path)
                if illustration_manifest_path.exists()
                else {}
            )
            add_check(
                checks,
                "illustration_manifest_exists",
                illustration_manifest_path.exists(),
                str(illustration_manifest_path),
            )
            add_check(
                checks,
                "illustration_crops_succeeded",
                illustration_manifest.get("failed_count") == 0,
                str(illustration_manifest.get("failed_count")),
            )
        if not audio_only:
            visual_report = build_visual_audit(
                job_dir,
                require_illustrations=policies.get("note_illustrations")
                == "cropped_regions_only",
                require_bindings=policies.get("note_illustrations")
                == "cropped_regions_only",
                require_note=args.stage == "final",
            )
            write_json(job_dir / "metadata" / "audit_visual.json", visual_report)
            add_check(
                checks,
                "visual_evidence_integrity",
                visual_report.get("passed") is True,
                "; ".join(
                    check["detail"]
                    for check in visual_report.get("checks", [])
                    if check.get("passed") is not True
                )
                or "timestamps, provenance, and bindings passed",
            )

    if args.stage == "final":
        note_path = job_dir / "note" / "note.md"
        note_text = note_path.read_text(encoding="utf-8") if note_path.exists() else ""
        add_check(checks, "note_exists", bool(note_text.strip()), str(note_path))
        local_targets = local_markdown_images(note_text)
        existing_targets = [
            target for target in local_targets if (note_path.parent / target).resolve().is_file()
        ]
        all_image_embeds = (
            re.findall(r"!\[[^\]]*\]\((?:<[^>]+>|[^\)]+)\)", note_text)
            + OBSIDIAN_IMAGE_RE.findall(note_text)
            + HTML_IMAGE_RE.findall(note_text)
        )
        if audio_only:
            add_check(
                checks,
                "audio_only_note_disclosure",
                AUDIO_ONLY_DISCLOSURE in note_text,
                AUDIO_ONLY_DISCLOSURE,
            )
            add_check(
                checks,
                "audio_only_note_has_no_image_embeds",
                not all_image_embeds,
                ", ".join(all_image_embeds) or "No image embeds",
            )
        elif policies.get("note_illustrations") == "cropped_regions_only":
            manifest_path = job_dir / "illustrations" / "manifest.json"
            manifest = read_json(manifest_path) if manifest_path.exists() else {}
            created_count = int(manifest.get("created_count") or 0)
            cropped_targets = [
                target
                for target in existing_targets
                if (note_path.parent / target).resolve().is_relative_to(job_dir / "illustrations")
            ]
            add_check(
                checks,
                "note_uses_only_cropped_illustrations",
                len(cropped_targets) == len(local_targets),
                ", ".join(cropped_targets) or "No cropped illustration references",
            )
            disclosure = (
                "无适合裁剪的插图" in note_text
                or "no suitable cropped illustration" in note_text.casefold()
            )
            add_check(
                checks,
                "note_has_meaningful_illustration_or_disclosure",
                bool(cropped_targets) if created_count else disclosure,
                f"created={created_count}; referenced={len(cropped_targets)}; disclosure={disclosure}",
            )
        else:
            add_check(
                checks,
                "note_has_local_image",
                bool(existing_targets),
                ", ".join(existing_targets) or "No existing local image reference",
            )
        add_check(
            checks,
            "note_has_coverage_section",
            "覆盖" in note_text or "coverage" in note_text.casefold(),
            "coverage disclosure",
        )

    passed = all(check["passed"] for check in checks)
    report = {"stage": args.stage, "passed": passed, "checks": checks}
    write_json(job_dir / "metadata" / f"audit_{args.stage}.json", report)
    print(f"{args.stage}: {'PASS' if passed else 'FAIL'}")
    for check in checks:
        print(f"- {'PASS' if check['passed'] else 'FAIL'} {check['name']}: {check['detail']}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
