#!/usr/bin/env python3
"""Publish an audited note with copied illustrations and optional media cleanup."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from audit_published_note import audit_published
from cleanup_job import cleanup_managed_media
from common import (
    is_audio_only_job,
    job_source_identity,
    read_json,
    sha256_file,
    update_pipeline_state,
    utc_now,
    write_json,
)


MD_IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<target><[^>]+>|[^)]+)\)")
UNSAFE_FILENAME_RE = re.compile(r"[<>:\"/\\|?*\x00-\x1f]")


def safe_component(value: str, fallback: str) -> str:
    cleaned = UNSAFE_FILENAME_RE.sub("-", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-")
    return (cleaned or fallback)[:140]


def display_date(value: str) -> str:
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def local_image_matches(note_text: str) -> list[re.Match[str]]:
    matches: list[re.Match[str]] = []
    for match in MD_IMAGE_RE.finditer(note_text):
        target = match.group("target").strip().strip("<>")
        if target.startswith(("http://", "https://", "data:")):
            continue
        matches.append(match)
    return matches


def yaml_frontmatter(job: dict[str, Any], metadata: dict[str, Any]) -> str:
    source = job.get("source", {})
    audio_only = is_audio_only_job(job)
    platform_name, source_id = job_source_identity(job, metadata)
    creator = metadata.get("channel") or metadata.get("uploader")
    values = {
        "type": f"{platform_name}-audio-note" if audio_only else f"{platform_name}-video-note",
        "platform": platform_name,
        "source": source.get("url"),
        "video_id": source_id,
        "bvid": source.get("bvid") if platform_name == "bilibili" else None,
        "creator": creator,
        "up": metadata.get("uploader") if platform_name == "bilibili" else None,
        "channel": creator if platform_name == "youtube" else None,
        "published": display_date(str(metadata.get("upload_date") or "")),
        "created": utc_now(),
        "evidence_mode": "audio_only" if audio_only else "full",
    }
    lines = ["---"]
    for key, value in values.items():
        if value:
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    platform_tag = "Bilibili" if platform_name == "bilibili" else "YouTube"
    lines.extend(["tags:", f"  - {platform_tag}", "  - audio-note" if audio_only else "  - video-note", "---", ""])
    return "\n".join(lines)


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    shutil.copy2(source, temporary)
    temporary.replace(destination)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-media", action="store_true")
    args = parser.parse_args()

    job_dir = args.job.expanduser().resolve()
    config = read_json(job_dir / "output" / "output_config.json")
    job = read_json(job_dir / "metadata" / "job.json")
    final_audit = read_json(job_dir / "metadata" / "audit_final.json")
    if final_audit.get("passed") is not True:
        parser.error("Run a passing final note audit before publication")
    if not is_audio_only_job(job):
        visual_audit_path = job_dir / "metadata" / "audit_visual.json"
        visual_audit = read_json(visual_audit_path) if visual_audit_path.exists() else {}
        visual_check_passed = any(
            check.get("name") == "visual_evidence_integrity" and check.get("passed") is True
            for check in final_audit.get("checks", [])
            if isinstance(check, dict)
        )
        if visual_audit.get("passed") is not True or not visual_check_passed:
            parser.error(
                "Run a passing visual-evidence semantic audit and final audit before publication"
            )
    internal_note = job_dir / "note" / "note.md"
    if not internal_note.is_file():
        parser.error(f"Internal note is missing: {internal_note}")
    note_text = internal_note.read_text(encoding="utf-8")

    metadata_path = job_dir / "metadata" / "source_metadata.json"
    metadata = read_json(metadata_path) if metadata_path.exists() else {}
    source = job.get("source", {})
    platform_name, source_id = job_source_identity(job, metadata)
    legacy_bvid = str(source.get("bvid") or source_id)
    title = safe_component(
        str(metadata.get("title") or source.get("title") or source_id),
        source_id,
    )
    upload_date = display_date(str(metadata.get("upload_date") or "undated"))
    try:
        filename = config["filename_template"].format(
            upload_date=upload_date,
            title=title,
            platform=platform_name,
            source_id=source_id,
            bvid=legacy_bvid,
        )
        namespace_text = config["asset_namespace"].format(
            platform=platform_name,
            source_id=source_id,
            bvid=legacy_bvid,
        )
    except (KeyError, ValueError) as exc:
        parser.error(f"Invalid output template: {exc}")
    filename = safe_component(Path(filename).stem, source_id) + ".md"
    namespace = Path(namespace_text)
    if namespace.is_absolute() or ".." in namespace.parts:
        parser.error("asset_namespace must be a safe relative path")

    note_path = Path(config["note_dir"]) / filename
    asset_root = Path(config["asset_dir"]) / namespace
    if note_path.exists():
        if config.get("on_conflict") == "fail":
            parser.error(f"Published note already exists: {note_path}")
        existing = note_path.read_text(encoding="utf-8", errors="replace")
        prior_path = job_dir / "output" / "publish_manifest.json"
        prior_owned = False
        if prior_path.exists():
            prior_owned = read_json(prior_path).get("note", {}).get("path") == str(note_path)
        if not prior_owned and source_id not in existing:
            parser.error("Refusing to update a note that is not owned by this source video")

    image_matches = local_image_matches(note_text)
    crop_only = job.get("policies", {}).get("note_illustrations") == "cropped_regions_only"
    asset_rows: list[dict[str, Any]] = []
    embed_by_target: dict[str, str] = {}
    destination_by_source: dict[Path, Path] = {}
    for match in image_matches:
        raw_target = match.group("target")
        target = raw_target.strip().strip("<>")
        source_path = (internal_note.parent / target).resolve()
        if not source_path.is_file() or not source_path.is_relative_to(job_dir):
            parser.error(f"Local note image is missing or outside the job: {target}")
        if crop_only and not source_path.is_relative_to(job_dir / "illustrations"):
            parser.error(f"Full-frame note images are forbidden; use a cropped illustration: {target}")
        destination = destination_by_source.get(source_path)
        if destination is None:
            destination = asset_root / f"{source_id}-{source_path.name}"
            destination_by_source[source_path] = destination
            if config["link_style"] == "obsidian-wikilink":
                vault_root = Path(config["vault_root"])
                vault_relative = destination.relative_to(vault_root).as_posix()
                embed = f"![[{vault_relative}|{match.group('alt')}]]"
            else:
                relative = Path(os.path.relpath(destination, note_path.parent)).as_posix()
                embed = f"![{match.group('alt')}](<{relative}>)"
            asset_rows.append(
                {
                    "source": str(source_path),
                    "destination": str(destination),
                    "embed": embed,
                }
            )
        embed_by_target[raw_target] = next(
            row["embed"] for row in asset_rows if row["destination"] == str(destination)
        )

    def replace_image(match: re.Match[str]) -> str:
        return embed_by_target.get(match.group("target"), match.group(0))

    published_text = MD_IMAGE_RE.sub(replace_image, note_text)
    if config["format"] == "obsidian" and not published_text.startswith("---\n"):
        published_text = yaml_frontmatter(job, metadata) + published_text

    plan = {
        "note": str(note_path),
        "assets": [row["destination"] for row in asset_rows],
        "cleanup_after_publish": bool(config.get("cleanup_after_publish") and not args.keep_media),
    }
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    for row in asset_rows:
        atomic_copy(Path(row["source"]), Path(row["destination"]))
        row["sha256"] = sha256_file(Path(row["destination"]))
        row["size"] = Path(row["destination"]).stat().st_size
    atomic_write(note_path, published_text)
    manifest = {
        "schema_version": "2.0",
        "published_at": utc_now(),
        "format": config["format"],
        "profile_name": config.get("profile_name"),
        "source_platform": platform_name,
        "source_id": source_id,
        "note": {
            "path": str(note_path),
            "sha256": sha256_file(note_path),
        },
        "assets": asset_rows,
        "audit": {"passed": False},
    }
    if platform_name == "bilibili":
        manifest["source_bvid"] = source_id
    write_json(job_dir / "output" / "publish_manifest.json", manifest)
    audit = audit_published(job_dir)
    if audit.get("passed") is not True:
        parser.error("Published-note audit failed; managed media was retained")
    update_pipeline_state(job_dir, "publish", "complete", str(note_path))

    if config.get("cleanup_after_publish") and not args.keep_media:
        cleanup_managed_media(job_dir)
    else:
        update_pipeline_state(job_dir, "cleanup", "complete", "Media retained by request")
    print(note_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
