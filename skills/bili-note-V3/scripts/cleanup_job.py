#!/usr/bin/env python3
"""Safely remove registered transient media after a successful publication audit."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from common import is_audio_only_job, read_json, update_pipeline_state, utc_now, write_json


CLEANUP_KINDS = {"source_video", "source_audio", "derived_audio"}


def verified_managed_path(job_dir: Path, relative_text: str) -> Path:
    relative = Path(relative_text)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe managed-media path: {relative_text}")
    raw_path = job_dir / relative
    if raw_path.is_symlink():
        raise ValueError(f"Refusing to delete a symlink: {relative_text}")
    resolved = raw_path.resolve()
    if not resolved.is_relative_to(job_dir):
        raise ValueError(f"Managed-media path escapes the job: {relative_text}")
    return resolved


def cleanup_managed_media(
    job_dir: Path,
    *,
    dry_run: bool = False,
    keep_media: bool = False,
) -> dict[str, Any]:
    job_dir = job_dir.resolve()
    final_audit_path = job_dir / "metadata" / "audit_final.json"
    publish_audit_path = job_dir / "output" / "audit_publish.json"
    if not final_audit_path.exists() or not publish_audit_path.exists():
        raise ValueError("Media cleanup requires passing final-note and published-note audits")
    final_audit = read_json(final_audit_path)
    publish_audit = read_json(publish_audit_path)
    if final_audit.get("passed") is not True or publish_audit.get("passed") is not True:
        raise ValueError("Media cleanup requires passing final-note and published-note audits")

    job = read_json(job_dir / "metadata" / "job.json")
    if not is_audio_only_job(job):
        visual_audit_path = job_dir / "metadata" / "audit_visual.json"
        if not visual_audit_path.exists():
            raise ValueError("Media cleanup requires a passing visual-evidence semantic audit")
        visual_audit = read_json(visual_audit_path)
        visual_check_passed = any(
            check.get("name") == "visual_evidence_integrity" and check.get("passed") is True
            for check in final_audit.get("checks", [])
            if isinstance(check, dict)
        )
        if visual_audit.get("passed") is not True or not visual_check_passed:
            raise ValueError("Media cleanup requires a passing visual-evidence semantic audit")
    policy = job.get("policies", {}).get("cleanup_source_media", "after_publish")
    manifest_path = job_dir / "metadata" / "media_manifest.json"
    manifest = (
        read_json(manifest_path)
        if manifest_path.exists()
        else {"schema_version": "1.0", "files": []}
    )
    candidates = [
        row
        for row in manifest.get("files", [])
        if row.get("managed") is True
        and row.get("cleanup_after_publish") is True
        and row.get("status") == "present"
        and row.get("kind") in CLEANUP_KINDS
    ]

    if keep_media or policy == "retain":
        report = {
            "schema_version": "1.0",
            "completed_at": utc_now(),
            "status": "retained",
            "dry_run": dry_run,
            "bytes_freed": 0,
            "files": [row.get("path") for row in candidates],
        }
        if not dry_run:
            write_json(job_dir / "output" / "cleanup_report.json", report)
            update_pipeline_state(job_dir, "cleanup", "complete", "Media retained by policy")
        return report

    planned: list[tuple[dict[str, Any], Path, int]] = []
    for row in candidates:
        path = verified_managed_path(job_dir, str(row.get("path") or ""))
        size = path.stat().st_size if path.is_file() else 0
        planned.append((row, path, size))

    report = {
        "schema_version": "1.0",
        "completed_at": utc_now(),
        "status": "planned" if dry_run else "deleted",
        "dry_run": dry_run,
        "bytes_freed": sum(size for _, _, size in planned),
        "files": [str(path.relative_to(job_dir)) for _, path, _ in planned],
    }
    if dry_run:
        return report

    for row, path, size in planned:
        if path.is_file():
            path.unlink()
        row["status"] = "deleted"
        row["deleted_at"] = report["completed_at"]
        row["bytes_freed"] = size
    manifest["updated_at"] = report["completed_at"]
    write_json(manifest_path, manifest)
    write_json(job_dir / "output" / "cleanup_report.json", report)
    update_pipeline_state(
        job_dir,
        "cleanup",
        "complete",
        f"Deleted {len(planned)} managed media files; freed {report['bytes_freed']} bytes",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-media", action="store_true")
    args = parser.parse_args()
    try:
        report = cleanup_managed_media(
            args.job.expanduser().resolve(),
            dry_run=args.dry_run,
            keep_media=args.keep_media,
        )
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    print(
        f"{report['status']}: {len(report['files'])} files, "
        f"{report['bytes_freed']} bytes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
