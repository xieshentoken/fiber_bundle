"""Explicit candidate import with durable exclusive publication and preserved assurance."""

import json
import sqlite3
import time
from typing import Any

from . import auth, expert_artifacts, expert_evidence, note_search_jobs, publication_files, vaults
from .config import Config, IntegrityError, InvalidInput, contained, make_directory
from .inputs import canonical
from .objects import digest
from .query_policy_store import write_private


def publish(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    identity: str,
    artifact_sha: str,
    source_reviewed: bool,
    candidate_acknowledged: bool,
    *,
    preview_only: bool = False,
    system_review_id: str | None = None,
) -> dict[str, Any]:
    source, packet, files = expert_evidence.load(config, db, client_id, identity)
    if artifact_sha != digest(canonical(packet).encode()):
        raise IntegrityError("expert_review_artifact_changed")
    system_review = None
    if system_review_id is not None:
        from .automatic_review import credential

        system_review = credential(
            config, db, system_review_id, "expert", identity, packet["source_sha256"], artifact_sha
        )
    elif source_reviewed is not True or candidate_acknowledged is not True:
        raise InvalidInput("expert_explicit_candidate_import_review_required")
    row = db.execute(
        "SELECT * FROM source_expert_publications WHERE task_id=?", (identity,)
    ).fetchone()
    from . import publication_layout, publication_locations

    if row is not None and row["state"] == "published":
        return {
            "state": "published",
            "relative_path": row["relative_path"],
            "historical": True,
            **publication_locations.wire(db, "expert", identity),
            "knowledge_verified": False,
            "review_state": "deferred",
        }
    version = db.execute(
        "SELECT source_version_id FROM receipts WHERE job_id=?", (source["receipt_id"],)
    ).fetchone()[0]
    intended = expert_evidence.render(
        {**dict(source), "version_id": version, "source_sha256": packet["source_sha256"]},
        packet,
        files,
    )
    plan = json.loads(source["plan_json"])
    markdown = [
        raw for name, raw in intended.items() if name.endswith(".md") and not name.startswith(".")
    ]
    if (
        any(len(raw) > plan["limits"]["markdown_bytes"] for raw in markdown)
        or sum(map(len, markdown)) > plan["limits"]["markdown_total_bytes"]
    ):
        raise InvalidInput("expert_host_note_capacity")
    vault = vaults.checked_vault(config, auth.grant(db, client_id, source["vault_id"], "add"))
    state_manifest = canonical(
        {
            "vault_key": [vault.stat().st_dev, vault.stat().st_ino],
            "files": expert_artifacts.manifest(intended),
        }
    )
    target_name = "Expert-" + identity
    stage_name = ".fiber-expert-" + identity
    assessment = canonical(
        json.loads(system_review["assessment_json"])
        if system_review
        else {
            "source_reviewed": True,
            "candidate_acknowledged": True,
            "semantic_review": "deferred",
            "knowledge_verified": False,
        }
    )
    if row is None:
        from .automatic_policy import get as processing_policy

        publication_layout.prepare(
            config,
            db,
            "expert",
            identity,
            source["vault_id"],
            intended,
            refresh_preview=preview_only,
            expert_inbox=processing_policy(db, source["vault_id"])["settings"].get("policy_version")
            == 2,
        )
        if preview_only:
            return publication_layout.preview_result(
                db, "expert", identity, target_name, vault_id=source["vault_id"], root=vault
            )

        db.execute(
            "INSERT INTO source_expert_publications VALUES(?,?,?,?,?,'prepared',?)",
            (identity, artifact_sha, assessment, target_name, state_manifest, time.time()),
        )
        row = db.execute(
            "SELECT * FROM source_expert_publications WHERE task_id=?", (identity,)
        ).fetchone()
    if (row["artifact_sha256"], row["assessment_json"], row["manifest_json"]) != (
        artifact_sha,
        assessment,
        state_manifest,
    ):
        raise IntegrityError("expert_publication_binding_changed")
    if row["state"] == "conflict":
        raise IntegrityError("expert_publication_conflict")
    if preview_only:
        return publication_layout.preview_result(
            db, "expert", identity, row["relative_path"], vault_id=source["vault_id"], root=vault
        )
    layout_result = publication_layout.publish(config, db, "expert", identity, client_id, intended)
    if layout_result is not None:
        db.execute(
            "UPDATE source_expert_publications SET state='published' WHERE task_id=?", (identity,)
        )
        from .note_search_jobs import record

        record(config, source["vault_id"], "pending", reason="document_publication")
        return {
            "state": "published",
            "relative_path": row["relative_path"],
            "knowledge_verified": False,
            **layout_result,
            "review_state": "deferred",
        }

    stage, target = contained(vault, stage_name), contained(vault, target_name)
    if row["state"] == "prepared":
        if stage.exists() or target.exists():
            db.execute(
                "UPDATE source_expert_publications SET state='conflict' WHERE task_id=?",
                (identity,),
            )
            raise IntegrityError("expert_publication_path_exists")
        db.execute(
            "UPDATE source_expert_publications SET state='publishing' WHERE task_id=?", (identity,)
        )
        stage.mkdir(mode=0o700)
        for name, raw in intended.items():
            path = contained(stage, name)
            make_directory(path.parent)
            write_private(path, raw)
    expected = expert_artifacts.manifest(intended)
    if target.exists():
        if stage.exists():
            raise IntegrityError("expert_publication_ambiguous")
    else:
        if expert_artifacts.manifest(expert_artifacts.tree(stage)) != expected:
            raise IntegrityError("expert_publication_staging_changed")
        publication_files.rename_exclusive(vault, stage_name, target_name)
    if (
        expert_artifacts.manifest(expert_artifacts.tree(target)) != expected
        or [vault.stat().st_dev, vault.stat().st_ino] != json.loads(state_manifest)["vault_key"]
    ):
        raise IntegrityError("expert_publication_changed")
    db.execute(
        "UPDATE source_expert_publications SET state='published' WHERE task_id=?", (identity,)
    )
    note_search_jobs.record(config, source["vault_id"], "pending", reason="document_publication")
    return {
        "state": "published",
        "relative_path": target_name,
        "knowledge_verified": False,
        "review_state": "deferred",
    }


def provenance(db: sqlite3.Connection, vault_id: str, path: str, sha: str) -> dict[str, Any] | None:
    rows = db.execute(
        "SELECT p.*,i.receipt_id,COALESCE(l.package_root,p.relative_path) AS current_package FROM "
        "source_expert_publications p JOIN source_imports i ON "
        "i.id=p.task_id LEFT JOIN publication_locations l ON l.kind='expert' AND "
        "l.publication_id=p.task_id "
        "WHERE COALESCE(l.vault_id,i.vault_id)=? AND p.state='published' "
        "AND "
        "substr(?,1,length(COALESCE(l.package_root,p.relative_path))+1)=CO"
        "ALESCE(l.package_root,p.relative_path)||'/'",
        (vault_id, path),
    )
    for row in rows:
        prefix = row["current_package"] + "/"
        if not path.startswith(prefix):
            continue
        name = path.removeprefix(prefix)
        binding = json.loads(row["manifest_json"])["files"].get(name)
        if binding is None or not name.endswith(".md") or name.startswith("."):
            continue
        version = db.execute(
            "SELECT source_version_id FROM receipts WHERE job_id=?", (row["receipt_id"],)
        ).fetchone()[0]
        return {
            "source_kind": "agent_analysis" if binding["sha256"] == sha else "user_revision",
            "expert_module_id": "expert:" + row["task_id"],
            "expert_module_version": row["artifact_sha256"],
            "expert_projection": binding["sha256"] == sha,
            "source_label": "完整 Skill 候选分析，独立语义审核待完成",
            "review_type": "system"
            if json.loads(row["assessment_json"]).get("actor") in {"system_rules", "system_model"}
            else "human",
            "review_actor": json.loads(row["assessment_json"]).get("actor", "local_admin"),
            "source_version_id": version,
            "independent_evidence": False,
            "artifact_stage": "draft",
            "review_state": "deferred",
            "knowledge_verified": False,
            "completeness_status": json.loads(row["assessment_json"]).get(
                "completeness_status", "unknown"
            ),
            "known_gaps": json.loads(row["assessment_json"]).get("known_gaps", []),
            "save_basis": json.loads(row["assessment_json"]).get("save_basis"),
        }
    return None
