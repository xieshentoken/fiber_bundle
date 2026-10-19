"""Read-only discovery of externally moved/replaced published notes in the allowed scope."""

import sqlite3
import stat
from typing import Any

from . import note_index, note_placement, publication_locations, publication_rebind, vault_scope
from .config import Config, IntegrityError
from .objects import digest
from .publication_file_io import read


def body_paths(location: dict[str, Any]) -> list[str]:
    plan = location["plan"]
    return [
        path
        for logical, path in plan["mapping"].items()
        if logical.endswith(".md") and logical in plan.get("body_files", list(plan["mapping"]))
    ]


def scan(config: Config, db: sqlite3.Connection, client: str, vault: str) -> dict[str, Any]:
    note_placement.guard(db, vault)
    root, scope, policy = publication_rebind.repair_context(config, db, client, vault)
    locations = []
    for row in note_placement.locations(db, vault):
        if row["state"] not in {"published", "missing"} or not publication_locations.committed(
            db, {**row, "state": "published"}
        ):
            continue
        bodies = body_paths(row)
        # Exclude before any filesystem inspection, including protected package children.
        if not bodies or any(not scope.allows(path) for path in row["plan"]["mapping"].values()):
            continue
        locations.append(row)

    metadata = {}
    for path, info in vault_scope.entries(root, scope):
        relative = path.relative_to(root).as_posix()
        if stat.S_ISREG(info.st_mode) and relative.endswith(".md") and scope.allows(relative):
            metadata[relative] = info

    issues = []
    for row in locations:
        changed = any(
            path not in metadata
            or [
                metadata[path].st_dev,
                metadata[path].st_ino,
                float(getattr(metadata[path], "st_birthtime", 0)).hex(),
            ]
            != row["plan"].get("file_keys", {}).get(path)
            for path in body_paths(row)
        )
        if changed or row["state"] == "missing":
            issues.append(row)

    expected_hashes = {
        row["plan"].get("write_hashes", row["plan"]["published_hashes"])[row["entry_note_path"]]
        for row in issues
    }
    hashes: dict[str, list[str]] = {}
    warnings = []
    if not policy["binding_current"]:
        warnings.append(
            "保存规则的目录绑定也已变化；本次核对不修改配置，后续保存或归档前请重新预览并确认保存规则。"
        )
    total = 0
    limits = note_index.Limits()
    # One bounded pass, no models, images, extraction, or sidecar mutation.
    for relative, info in metadata.items() if issues else []:
        if info.st_size > limits.note_bytes or total + info.st_size > 256 * 1024 * 1024:
            warnings.append("部分过大正文未作摘要匹配，可手动填写当前位置。")
            continue
        total += info.st_size
        try:
            raw, _ = read(root, relative)
        except (OSError, IntegrityError):
            warnings.append("部分文件正在变化或暂不可读，请保存编辑后重新扫描。")
            continue
        sha = digest(raw)
        if sha in expected_hashes:
            hashes.setdefault(sha, []).append(relative)

    output = []
    for row in issues:
        source = row["entry_note_path"]
        key = row["plan"].get("file_keys", {}).get(source)
        by_identity = [
            path
            for path, info in metadata.items()
            if [info.st_dev, info.st_ino, float(getattr(info, "st_birthtime", 0)).hex()] == key
        ]
        sha = row["plan"].get("write_hashes", row["plan"]["published_hashes"])[source]
        candidates = {path: "content_hash" for path in hashes.get(sha, [])} | {
            path: "file_identity" for path in by_identity
        }
        if source in metadata:
            candidates[source] = "original_path"
        # Flat documents can be renamed. Packages must remain intact with note.md as entry.
        if row["package_root"]:
            candidates = {
                path: reason for path, reason in candidates.items() if path.endswith("/note.md")
            }
        found = [{"path": p, "evidence": reason} for p, reason in sorted(candidates.items())]
        if row["state"] == "missing" and not found:
            continue  # Already acknowledged absent; do not ask the user to process it again.
        present = [p for p in body_paths(row) if p in metadata]
        state = (
            "replaced"
            if source in metadata
            else "ambiguous"
            if len(found) > 1
            else "moved"
            if found
            else "partial"
            if present
            else "unlocated"
        )
        output.append(
            {
                "kind": row["kind"],
                "publication_id": row["publication_id"],
                "expected_revision": row["revision"],
                "source_path": source,
                "state": state,
                "candidates": found,
                "suggested_path": found[0]["path"] if len(found) == 1 else None,
                "can_mark_missing": row["state"] == "published" and not present and not found,
            }
        )
    from . import note_identity_rebind

    output.extend(
        note_identity_rebind.issues(note_identity_rebind.context(config, db, client, vault))
    )
    batches = []
    import json

    for row in db.execute(
        "SELECT id,state,payload FROM vault_operations WHERE vault_id=? "
        "AND kind='publication_reconcile_batch' ORDER BY created_at DESC LIMIT 20",
        (vault,),
    ):
        payload = json.loads(row["payload"])
        if payload["client_id"] == client:
            batches.append({"operation_id": row["id"], "state": row["state"]})
    return {
        "items": output,
        "warnings": sorted(set(warnings)),
        "batches": batches,
        "vault_write_performed": False,
        "model_calls": 0,
    }
