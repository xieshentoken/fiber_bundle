"""Canonical published modules authorized independently of import ownership."""

import json
import sqlite3
from typing import Any

from . import auth, expert_artifacts, publication_locations, vaults
from .config import Config, IntegrityError, InvalidInput, contained
from .inputs import canonical
from .objects import digest
from .publication_file_io import read
from .vault_scope import for_vault


def load(
    config: Config,
    db: sqlite3.Connection,
    cid: str,
    identity: str,
    expected: str | None = None,
    *,
    file_names: set[str] | None = None,
    text_only: bool = False,
) -> dict[str, Any]:
    from .agent_connections.visibility import scope, visible
    from .organization import packages
    from .organization import store as organization_store

    if organization_store.get(config, "packages", identity) is not None:
        return dict(
            packages.load(
                config, db, cid, identity, expected, file_names=file_names, text_only=text_only
            )
        )

    row = db.execute(
        "SELECT p.*,i.vault_id,i.receipt_id,i.plan_json FROM source_expert_publications p "
        "JOIN source_imports i ON i.id=p.task_id WHERE p.task_id=? AND p.state='published'",
        (identity,),
    ).fetchone()
    if row is None:
        raise auth.Denied(404)
    location = publication_locations.get(db, "expert", identity)
    vid = location["vault_id"] if location else row["vault_id"]
    _, quality = scope(config, db, cid, [vid])
    vault = auth.grant(db, cid, vid, "query")
    root = vaults.checked_vault(config, vault)
    assessment = json.loads(row["assessment_json"])
    if not visible(assessment, quality):
        raise auth.Denied(404)
    if expected is not None and expected != row["artifact_sha256"]:
        raise InvalidInput("knowledge_version_stale")
    if location and not publication_locations.committed(db, location):
        raise auth.Denied(404)
    entry = location["entry_note_path"] if location else row["relative_path"] + "/note.md"
    path_scope = for_vault(root, vault)
    if not path_scope.allows(entry):
        raise auth.Denied(404)
    declared = json.loads(row["manifest_json"])["files"]
    files: dict[str, bytes] = {}
    total = 0
    for logical, bound in declared.items():
        if not logical.startswith(".fiber-expert-canonical/"):
            continue
        name = logical.removeprefix(".fiber-expert-canonical/")
        if file_names is not None and name not in file_names:
            continue
        if text_only and not (
            name
            in {
                "manifest.json",
                "references/knowledge/index.json",
                "references/evidence/anchors.jsonl",
                "references/runtime/catalog.json",
            }
            or name.startswith("scripts/")
            and name.endswith(".py")
        ):
            continue
        relative = (
            location["plan"]["mapping"].get(logical)
            if location
            else row["relative_path"] + "/" + logical
        )
        if not relative or not path_scope.allows(relative):
            raise auth.Denied(404)
        data, _ = read(root, relative)
        if len(data) != bound["byte_count"] or digest(data) != bound["sha256"]:
            raise IntegrityError("knowledge_module_changed")
        total += len(data)
        if len(files) >= 2000 or total > expert_artifacts.MAX_TREE:
            raise InvalidInput("knowledge_module_capacity")
        name = logical.removeprefix(".fiber-expert-canonical/")
        contained(root, name)
        files[name] = data
    required = {
        "manifest.json",
        "references/knowledge/index.json",
        "references/evidence/anchors.jsonl",
        "scripts/query_reference.py",
    }
    if file_names is None and not required <= set(files):
        raise IntegrityError("knowledge_module_invalid")
    package = json.loads(files["manifest.json"])
    if package["package"]["status"] != "draft":
        raise IntegrityError("knowledge_module_assurance_changed")
    index = json.loads(files.get("references/knowledge/index.json", b'{"objects": []}'))
    objects = []
    for item in index["objects"]:
        if item["path"] not in files:
            if text_only or file_names is not None:
                continue
            raise IntegrityError("knowledge_module_invalid")
        objects.append(json.loads(files[item["path"]]))
    anchors = [
        json.loads(line)
        for line in files.get("references/evidence/anchors.jsonl", b"").splitlines()
    ]
    catalog = (
        json.loads(files["references/runtime/catalog.json"])
        if "references/runtime/catalog.json" in files
        else None
    )
    if text_only and catalog:
        objects = catalog.get("objects", [])
    version = db.execute(
        "SELECT source_version_id FROM receipts WHERE job_id=?", (row["receipt_id"],)
    ).fetchone()[0]
    return {
        "module_id": "expert:" + identity,
        "version": row["artifact_sha256"],
        "vault_id": vid,
        "entry_note_path": entry,
        "source_version_id": version,
        "objects": objects,
        "object_count": len(index["objects"]),
        "catalog": catalog,
        "anchors": anchors,
        "files": files,
        "runtime": json.loads(row["plan_json"])["policy"]["expert_runtime"],
        "artifact_stage": "draft",
        "review_state": "deferred",
        "knowledge_verified": False,
        "independent_evidence": False,
        "completeness_status": assessment.get("completeness_status", "unknown"),
        "known_gaps": assessment.get("known_gaps", []),
        "save_basis": assessment.get("save_basis"),
        "package_sha256": digest(
            canonical(
                {
                    n.removeprefix(".fiber-expert-canonical/"): b
                    for n, b in declared.items()
                    if n.startswith(".fiber-expert-canonical/")
                }
            ).encode()
        ),
    }


def public(module: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v
        for k, v in module.items()
        if k not in {"files", "runtime", "objects", "anchors", "catalog"}
    } | {
        "object_count": module.get("object_count", len(module["objects"])),
        "anchor_count": len(module["anchors"]),
    }


def text(
    config: Config, db: sqlite3.Connection, cid: str, identity: str, expected: str | None = None
) -> dict[str, Any]:
    """Load only the published version's text catalog and read-only runtime scripts."""
    return load(config, db, cid, identity, expected, text_only=True)


def member(
    config: Config,
    db: sqlite3.Connection,
    cid: str,
    identity: str,
    expected: str,
    name: str,
    *,
    original: bool = False,
) -> bytes:
    # Authorization occurs before reading a requested binary member.
    if original:
        metadata = load(config, db, cid, identity, expected, file_names={"manifest.json"})
        auth.grant(db, cid, metadata["vault_id"], "original")
    module = load(config, db, cid, identity, expected, file_names={"manifest.json", name})
    if name not in module["files"]:
        raise auth.Denied(404)
    raw = module["files"][name]
    assert isinstance(raw, bytes)
    return raw
