"""Authorize indexed expert entry metadata without opening package members."""

import json
import re
import sqlite3
from pathlib import Path, PurePosixPath
from typing import Any

from .. import auth, note_changes, note_index, publication_locations
from ..agent_connections.visibility import scope, visible
from ..config import Config, IntegrityError
from ..organization import store
from ..vault_scope import for_vault, prefix
from . import cloud_visibility
from .contracts import fingerprint


def read(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vault_id: str,
    root: Path,
    notes: dict[str, note_index.Note],
    indexed: dict[str, tuple[str, str]],
) -> tuple[dict[str, dict[str, Any]], int, str]:
    _, quality = scope(config, db, client, [vault_id])
    if quality != "all":
        return {}, 0, fingerprint(["quality_restricted"])
    vault = auth.grant(db, client, vault_id, "query")
    allowed = for_vault(root, vault)
    root_key = [root.stat().st_dev, root.stat().st_ino]
    candidates: dict[str, dict[str, Any]] = {}
    with store.connection(config) as side:
        if side and side.execute("SELECT 1 FROM sqlite_master WHERE name='packages'").fetchone():
            for row in side.execute(
                "SELECT id,json_extract(value,'$.vault_id'),json_extract(value,'$.path'),"
                "json_extract(value,'$.title'),json_extract(value,'$.version'),"
                "json_extract(value,'$.vault_key'),json_extract(value,'$.artifact_stage'),"
                "json_extract(value,'$.review_state'),json_extract(value,'$.knowledge_verified') "
                ",json_extract(value,'$.files.\"SKILL.md\".sha256') "
                "FROM packages WHERE vault=? ORDER BY id",
                (vault_id,),
            ):
                identity, vid, path, title, version, key, stage, review, verified, entry_sha = row
                if vid != vault_id or not isinstance(path, str) or prefix(path) != path:
                    continue
                if (
                    not allowed.allows(path + "/manifest.json")
                    or json.loads(key or "null") != root_key
                ):
                    continue
                note_changes.identifier(identity)
                candidates[identity] = {
                    "id": "expert:" + identity,
                    "path": path,
                    "label": title
                    if isinstance(title, str) and title
                    else PurePosixPath(path).name,
                    "version": version,
                    "entry_kind": "directory",
                    "artifact_stage": stage if isinstance(stage, str) else "draft",
                    "review_state": review if isinstance(review, str) else "deferred",
                    "knowledge_verified": verified == 1,
                    "link_paths": [path + "/SKILL.md"]
                    if isinstance(entry_sha, str)
                    and re.fullmatch(r"[a-f0-9]{64}", entry_sha)
                    and allowed.allows(path + "/SKILL.md")
                    else [],
                }
    for row in db.execute(
        "SELECT p.task_id,p.artifact_sha256,p.relative_path,p.assessment_json,"
        "COALESCE(l.entry_note_path,p.relative_path||'/note.md') AS entry,"
        "COALESCE(l.package_root,p.relative_path) AS package_root "
        "FROM source_expert_publications p JOIN source_imports i ON i.id=p.task_id "
        "LEFT JOIN publication_locations l ON l.kind='expert' AND l.publication_id=p.task_id "
        "WHERE p.state='published' AND COALESCE(l.vault_id,i.vault_id)=? ORDER BY p.task_id",
        (vault_id,),
    ):
        path = row["entry"]
        if not isinstance(path, str) or prefix(path) != path or not allowed.allows(path):
            continue
        assessment = json.loads(row["assessment_json"])
        if not visible(assessment, quality):
            continue
        location = publication_locations.get(db, "expert", row["task_id"])
        if location and not publication_locations.committed(db, location):
            continue
        # Analysis audience restrictions also apply if an entry shares a saved
        # analysis path. Never revive a filtered title through expert metadata.
        entry_note = notes.get(path)
        if not cloud_visibility.path_allowed(
            db, client, vault_id, path, entry_note.sha256 if entry_note else ""
        ):
            continue
        note_changes.identifier(row["task_id"])
        candidates[row["task_id"]] = {
            "id": "expert:" + row["task_id"],
            "path": path,
            "label": entry_note.title if entry_note else PurePosixPath(row["package_root"]).name,
            "version": row["artifact_sha256"],
            "entry_kind": "markdown",
            "artifact_stage": assessment.get("artifact_stage", "draft"),
            "review_state": assessment.get("review_state", "deferred"),
            "knowledge_verified": assessment.get("knowledge_verified") is True,
            "link_paths": [path],
        }
    result = {}
    pending = 0
    for identity, metadata in candidates.items():
        version = metadata["version"]
        if not isinstance(version, str) or not re.fullmatch(r"[a-f0-9]{64}", version):
            raise IntegrityError("note cloud expert metadata invalid")
        if indexed.get(identity) == (version, metadata["path"]):
            result[identity] = metadata
        else:
            pending += 1
    return result, pending, fingerprint(candidates)
