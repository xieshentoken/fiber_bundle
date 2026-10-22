"""Explicit Obsidian iCloud admission; never download or traverse protected entries."""

import json
import os
from pathlib import Path
from typing import Any

from .. import cloud_files, vault_scope
from ..inputs import canonical
from . import files

OBSIDIAN = Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents"


def location(path: Path) -> None:
    if "Mobile Documents" in path.parts:
        if path != OBSIDIAN and OBSIDIAN not in path.parents:
            files.reject("icloud_container_required")
        cloud_files.require_available(path)
    else:
        files.local(path)


def absent(root: Path, excluded: tuple[str, ...]) -> None:
    for relative in excluded:
        # Inspect only the authorized parent listing, never the denied entry's metadata.
        prefix = vault_scope.prefix(relative)
        parts = prefix.split("/")
        parent = root
        for index, name in enumerate(parts):
            cloud_files.require_local(parent)
            with os.scandir(parent) as entries:
                present = any(entry.name == name for entry in entries)
            if not present:
                break
            if index == len(parts) - 1:
                files.reject("protected_entries_present")
            parent = parent / name


def scopes(source: Path, target: Path, row: Any) -> tuple[dict[str, Any], str]:
    raw = json.loads(row["scope_json"])
    before = vault_scope.effective(source, raw)
    if before.include != ("",) or set(before.exclude) - {"01-Projects/GF_supply_chain"}:
        files.reject("full_scope_required")
    absent(source, before.exclude)
    destination_scope = {"include": [""], "exclude": list(before.exclude)}
    after = vault_scope.effective(target, destination_scope)
    if after.wire() != before.wire():
        files.reject("policy_changed")
    return before.wire(), canonical(destination_scope)


def available(root: Path, excluded: tuple[str, ...]) -> None:
    location(root)
    absent(root, excluded)
    if "Mobile Documents" in root.parts:
        # The helper filters excluded names before lstat/resource metadata access.
        cloud_files.require_available(root, tree=True, exclude=excluded)
