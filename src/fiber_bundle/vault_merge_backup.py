"""Authenticate every encrypted backup record before allowing a destructive visibility switch."""

import hashlib
from pathlib import Path
from typing import Any

from .backup_bundle import layout, section_entries
from .backup_crypto import CHUNK, Records
from .backup_preflight import DEFAULT_LIMITS
from .backup_restore import _manifest
from .config import IntegrityError
from .inputs import canonical


def verify(path: Path, password: str, plan: dict[str, Any]) -> dict[str, Any]:
    marker = layout(path)
    manifest = None
    for part in ("state", "vaults"):
        with (path / (part + ".fbenc")).open("rb") as stream:
            reader = Records(stream, password, write=False)
            raw = reader.get()
            current, _ = _manifest(raw, path.parent, marker, DEFAULT_LIMITS)
            if manifest is not None and current != manifest:
                raise IntegrityError("vault_merge_backup_sections_disagree")
            manifest = current
            for entry in section_entries(current, part):
                if entry["kind"] != "file":
                    continue
                remaining = entry["bytes"]
                sha = hashlib.sha256()
                while remaining:
                    data = reader.get()
                    if len(data) != min(CHUNK, remaining):
                        raise IntegrityError("vault_merge_backup_frame_invalid")
                    sha.update(data)
                    remaining -= len(data)
                if sha.hexdigest() != entry["sha256"]:
                    raise IntegrityError("vault_merge_backup_file_hash_invalid")
            reader.finish(current["sha256"])
    assert manifest is not None
    entries = {(e["section"], e["path"]): e for e in manifest["entries"] if e["kind"] == "file"}
    ids = {v["id"] for v in manifest["vaults"]}
    for source in plan["inventories"]:
        vid = source["context"]["vault_id"]
        if vid not in ids:
            raise IntegrityError("vault_merge_backup_vault_missing")
        for file in source["files"]:
            checked_entry = entries.get(("vault:" + vid, file["path"]))
            if checked_entry is None or (checked_entry["bytes"], checked_entry["sha256"]) != (
                file["bytes"],
                file["sha256"],
            ):
                raise IntegrityError("vault_merge_backup_scope_or_bytes_changed")
        for version in source["sources"]:
            for asset in [*version["assets"], version["raw_event"]]:
                checked_entry = entries.get(("state", asset["relative_path"]))
                if checked_entry is None or (checked_entry["bytes"], checked_entry["sha256"]) != (
                    asset["byte_count"],
                    asset["sha256"],
                ):
                    raise IntegrityError("vault_merge_backup_original_missing_or_changed")
    if ("state", "metadata.sqlite3") not in entries:
        raise IntegrityError("vault_merge_complete_backup_required")
    return {
        "bundle_id": marker["bundle_id"],
        "scope_sha256": marker["scope_sha256"],
        "completion_sha256": hashlib.sha256(canonical(marker).encode()).hexdigest(),
    }
