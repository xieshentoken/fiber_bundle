#!/usr/bin/env python3
"""Verify a published note, copied illustrations, hashes, and rewritten embeds."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from common import is_audio_only_job, read_json, sha256_file, utc_now, write_json


def add_check(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def audit_published(job_dir: Path) -> dict[str, Any]:
    job_dir = job_dir.resolve()
    manifest_path = job_dir / "output" / "publish_manifest.json"
    checks: list[dict[str, Any]] = []
    add_check(checks, "publish_manifest_exists", manifest_path.is_file(), str(manifest_path))
    if not manifest_path.is_file():
        report = {"schema_version": "1.0", "passed": False, "checks": checks}
        write_json(job_dir / "output" / "audit_publish.json", report)
        return report

    manifest = read_json(manifest_path)
    note = manifest.get("note", {})
    note_path = Path(str(note.get("path") or "")).expanduser()
    note_exists = note_path.is_file()
    add_check(checks, "published_note_exists", note_exists, str(note_path))
    note_text = note_path.read_text(encoding="utf-8") if note_exists else ""
    note_hash = sha256_file(note_path) if note_exists else ""
    add_check(
        checks,
        "published_note_hash",
        note_exists and note_hash == note.get("sha256"),
        note_hash,
    )
    add_check(
        checks,
        "published_note_has_no_job_paths",
        str(job_dir) not in note_text,
        "no internal job path" if str(job_dir) not in note_text else str(job_dir),
    )

    assets = manifest.get("assets", [])
    missing_assets: list[str] = []
    changed_assets: list[str] = []
    missing_embeds: list[str] = []
    for asset in assets:
        destination = Path(str(asset.get("destination") or "")).expanduser()
        if not destination.is_file():
            missing_assets.append(str(destination))
            continue
        if sha256_file(destination) != asset.get("sha256"):
            changed_assets.append(str(destination))
        embed = str(asset.get("embed") or "")
        if embed not in note_text:
            missing_embeds.append(embed)
    add_check(checks, "published_assets_exist", not missing_assets, ", ".join(missing_assets))
    add_check(checks, "published_asset_hashes", not changed_assets, ", ".join(changed_assets))
    add_check(checks, "published_embeds_resolve", not missing_embeds, ", ".join(missing_embeds))
    job = read_json(job_dir / "metadata" / "job.json")
    if is_audio_only_job(job):
        add_check(
            checks,
            "audio_only_has_no_published_assets",
            not assets,
            str(len(assets)),
        )

    passed = all(check["passed"] for check in checks)
    report = {
        "schema_version": "1.0",
        "audited_at": utc_now(),
        "passed": passed,
        "note": str(note_path),
        "asset_count": len(assets),
        "checks": checks,
    }
    write_json(job_dir / "output" / "audit_publish.json", report)
    manifest["audit"] = {"passed": passed, "audited_at": report["audited_at"]}
    write_json(manifest_path, manifest)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    args = parser.parse_args()
    report = audit_published(args.job.expanduser().resolve())
    print(f"published: {'PASS' if report['passed'] else 'FAIL'}")
    for check in report["checks"]:
        print(f"- {'PASS' if check['passed'] else 'FAIL'} {check['name']}: {check['detail']}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
