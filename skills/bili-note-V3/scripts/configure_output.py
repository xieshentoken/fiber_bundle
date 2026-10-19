#!/usr/bin/env python3
"""Configure deterministic Markdown or Obsidian publication destinations."""

from __future__ import annotations

import argparse
from pathlib import Path

from common import read_json, utc_now, write_json


def find_obsidian_vault(path: Path) -> Path | None:
    current = path.expanduser().resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".obsidian").is_dir():
            return candidate
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument("--format", choices=("markdown", "obsidian"), required=True)
    parser.add_argument("--note-dir", type=Path, required=True)
    parser.add_argument("--asset-dir", type=Path)
    parser.add_argument("--profile-name", default="default")
    parser.add_argument(
        "--filename-template",
        default="{upload_date}-{title}-{source_id}.md",
    )
    parser.add_argument(
        "--asset-namespace",
        default="{source_id}",
        help="Subdirectory below asset-dir; supports {platform}, {source_id}, and legacy {bvid}",
    )
    parser.add_argument(
        "--link-style",
        choices=("auto", "standard-relative", "obsidian-wikilink"),
        default="auto",
    )
    parser.add_argument("--on-conflict", choices=("fail", "update"), default="update")
    cleanup = parser.add_mutually_exclusive_group()
    cleanup.add_argument(
        "--cleanup-media",
        dest="cleanup_after_publish",
        action="store_true",
        help="Delete registered source media and derived ASR audio after publish audit",
    )
    cleanup.add_argument(
        "--keep-media",
        dest="cleanup_after_publish",
        action="store_false",
    )
    parser.set_defaults(cleanup_after_publish=None)
    args = parser.parse_args()

    job_dir = args.job.expanduser().resolve()
    job = read_json(job_dir / "metadata" / "job.json")
    note_dir = args.note_dir.expanduser().resolve()
    asset_dir = (
        args.asset_dir.expanduser().resolve()
        if args.asset_dir
        else note_dir / "assets"
    )

    link_style = args.link_style
    vault_root: Path | None = None
    if args.format == "obsidian":
        if args.asset_dir is None:
            parser.error("Obsidian output requires an explicit --asset-dir")
        note_vault = find_obsidian_vault(note_dir)
        asset_vault = find_obsidian_vault(asset_dir)
        if note_vault is None or asset_vault is None:
            parser.error("Both Obsidian destinations must be inside a vault containing .obsidian")
        if note_vault != asset_vault:
            parser.error("The note and attachment directories must be in the same Obsidian vault")
        vault_root = note_vault
        if link_style == "auto":
            link_style = "obsidian-wikilink"
        if link_style != "obsidian-wikilink":
            parser.error("Obsidian output currently requires vault-relative wikilinks")
    else:
        if link_style == "auto":
            link_style = "standard-relative"
        if link_style != "standard-relative":
            parser.error("Portable Markdown output requires standard relative image links")

    policy = job.get("policies", {}).get("cleanup_source_media", "after_publish")
    cleanup_after_publish = (
        args.cleanup_after_publish
        if args.cleanup_after_publish is not None
        else policy == "after_publish"
    )
    config = {
        "schema_version": "1.0",
        "configured_at": utc_now(),
        "profile_name": args.profile_name,
        "format": args.format,
        "note_dir": str(note_dir),
        "asset_dir": str(asset_dir),
        "vault_root": str(vault_root) if vault_root else None,
        "link_style": link_style,
        "filename_template": args.filename_template,
        "asset_namespace": args.asset_namespace,
        "on_conflict": args.on_conflict,
        "cleanup_after_publish": cleanup_after_publish,
    }
    config_path = job_dir / "output" / "output_config.json"
    write_json(config_path, config)
    print(config_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
