"""Explicit analysis publication, fixed disclosure scope, and no-replace reconciliation."""

import json
import sqlite3
import time
from typing import Any
from uuid import UUID

from . import auth, database, publication_files, vaults
from . import document_creation_artifacts as artifacts
from . import document_creation_tasks as tasks
from .config import Config, IntegrityError, InvalidInput, contained
from .inputs import AssetInput, canonical
from .objects import digest
from .store import Store


def _readers(db: sqlite3.Connection, vault_id: str) -> list[str]:
    return [
        r[0]
        for r in db.execute(
            "SELECT g.client_id FROM client_vault_grants g JOIN clients c ON c.id=g.client_id "
            "WHERE g.vault_id=? AND c.active=1 AND g.can_query=1 ORDER BY g.client_id",
            (vault_id,),
        )
    ]


def _checked(row: sqlite3.Row) -> dict[str, Any]:
    try:
        value = json.loads(row["intent_json"])
        run_id = str(UUID(value["run_id"]))
        if (
            value["schema"] != "document-analysis-save/v1"
            or run_id != row["run_id"]
            or value["vault_id"] != row["vault_id"]
            or value["relative_path"] != "Analysis-" + run_id
            or value["staging_path"] != ".fiber-analysis-" + run_id
            or set(value["files"])
            not in (
                {"note.md", "draft.md", "provenance.json", ".fiber-analysis.json"},
                {"note.md", "draft.txt", "provenance.json", ".fiber-analysis.json"},
            )
            or any(
                not isinstance(value[key], list)
                or any(not isinstance(v, str) or not v for v in value[key])
                for key in ("readers", "source_vault_ids")
            )
            or not isinstance(value["expansion_readers"], list)
            or not set(value["expansion_readers"]).issubset(value["readers"])
        ):
            raise ValueError()
    except (ValueError, KeyError, TypeError, AttributeError):
        raise IntegrityError("document_save_intent_changed") from None
    if (
        not isinstance(value, dict)
        or canonical(value) != row["intent_json"]
        or digest(row["intent_json"].encode()) != row["intent_sha256"]
    ):
        raise IntegrityError("document_save_intent_changed")
    return value


def preview(
    config: Config, db: sqlite3.Connection, client_id: str, task_id: str, target_vault_id: str
) -> tuple[dict[str, Any], dict[str, bytes]]:
    raw, metadata = artifacts.get(config, db, client_id, task_id)
    plan = tasks.plan(config, db, client_id, task_id)
    root = vaults.checked_vault(config, auth.grant(db, client_id, target_vault_id, "add"))
    source = plan.value()["input"]
    readers = _readers(db, target_vault_id)
    allowed = set(readers)
    for vault_id in source["vault_ids"]:
        auth.grant(db, client_id, vault_id, "query")
        allowed &= set(_readers(db, vault_id))
    note = (
        "---\nsource_kind: agent_analysis\nknowledge_verified: false\n"
        "unverified_draft: true\ndocument_run_id: "
        + metadata["run_id"]
        + "\n---\n\n# Agent analysis — 未核验草稿\n\n"
        + raw.decode()
    ).encode()
    if len(note) > 1024 * 1024:
        raise InvalidInput("document_analysis_note_too_large")
    provenance = {
        "schema": "document-analysis-provenance/v1",
        "task_id": task_id,
        "plan_sha256": plan.sha256,
        "input_sha256": metadata["input_sha256"],
        "draft_version": metadata,
        "source_snapshot": source,
        "independent_evidence": False,
        "knowledge_verified": False,
    }
    files = {
        "note.md": note,
        metadata["artifact"]["original_name"]: raw,
        "provenance.json": canonical(provenance).encode(),
        ".fiber-analysis.json": canonical({"schema": 1, "run_id": metadata["run_id"]}).encode(),
    }
    value = {
        "schema": "document-analysis-save/v1",
        "run_id": metadata["run_id"],
        "client_id": client_id,
        "task_id": task_id,
        "vault_id": target_vault_id,
        "vault_key": [root.stat().st_dev, root.stat().st_ino],
        "readers": readers,
        "source_vault_ids": source["vault_ids"],
        "expansion_readers": sorted(set(readers) - allowed),
        "relative_path": "Analysis-" + metadata["run_id"],
        "staging_path": ".fiber-analysis-" + metadata["run_id"],
        "files": {n: {"sha256": digest(b), "byte_count": len(b)} for n, b in files.items()},
    }
    return value, files


def prepare(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    task_id: str,
    target_vault_id: str,
    *,
    expected_artifact_sha256: str,
    allow_save: bool = False,
) -> dict[str, Any]:
    if allow_save is not True:
        raise InvalidInput("document_save_not_requested")
    from .document_creation_binding import owned

    bound = owned(db, client_id, task_id)
    previous = db.execute(
        "SELECT * FROM document_creation_saves WHERE run_id=?", (bound["run_id"],)
    ).fetchone()
    if previous is not None and previous["state"] == "published":
        saved = _checked(previous)
        name = next(n for n in saved["files"] if n.startswith("draft."))
        if (
            saved["vault_id"] != target_vault_id
            or saved["files"][name]["sha256"] != expected_artifact_sha256
        ):
            raise InvalidInput("document_save_draft_conflict")
        auth.grant(db, client_id, target_vault_id, "add")
        for source in saved["source_vault_ids"]:
            auth.grant(db, client_id, source, "query")
        return {"run_id": bound["run_id"], "state": "published", "historical": True}
    value, _ = preview(config, db, client_id, task_id, target_vault_id)
    filename = next(n for n in value["files"] if n.startswith("draft."))
    if value["files"][filename]["sha256"] != expected_artifact_sha256:
        raise InvalidInput("document_save_draft_conflict")
    row = db.execute(
        "SELECT * FROM document_creation_saves WHERE run_id=?", (value["run_id"],)
    ).fetchone()
    raw = canonical(value)
    if row is None:
        with database.transaction(db):
            db.execute(
                "INSERT INTO document_creation_saves VALUES (?,?,?,?,NULL,NULL,'prepared',?)",
                (value["run_id"], target_vault_id, raw, digest(raw.encode()), time.time()),
            )
    elif canonical(_checked(row)) != raw:
        raise InvalidInput("document_save_scope_changed")
    approval = canonical(
        {
            "intent_sha256": digest(raw.encode()),
            "readers": value["readers"],
            "expansion_readers": value["expansion_readers"],
        }
    )
    row = db.execute(
        "SELECT approval_json FROM document_creation_saves WHERE run_id=?", (value["run_id"],)
    ).fetchone()
    return {
        "run_id": value["run_id"],
        "state": "scope_approval_required"
        if value["expansion_readers"] and row["approval_json"] != approval
        else "prepared",
        "intent_sha256": digest(raw.encode()),
        "knowledge_verified": False,
    }


def approve(
    db: sqlite3.Connection,
    run_id: str,
    expected_intent_sha256: str,
    *,
    admin_cookie: str,
    csrf: str,
) -> dict[str, Any]:
    auth.admin(db, admin_cookie, csrf)
    row = db.execute("SELECT * FROM document_creation_saves WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        raise auth.Denied(404)
    value = _checked(row)
    if (
        row["intent_sha256"] != expected_intent_sha256
        or _readers(db, row["vault_id"]) != value["readers"]
    ):
        raise InvalidInput("document_save_scope_changed")
    raw = canonical(
        {
            "intent_sha256": expected_intent_sha256,
            "readers": value["readers"],
            "expansion_readers": value["expansion_readers"],
        }
    )
    with database.transaction(db):
        db.execute(
            "UPDATE document_creation_saves SET approval_json=? WHERE run_id=?", (raw, run_id)
        )
    return {"run_id": run_id, "state": "approved"}


def publish(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    task_id: str,
    *,
    allow_save: bool = False,
) -> dict[str, Any]:
    if allow_save is not True:
        raise InvalidInput("document_save_not_requested")
    # Current authorization is required even for reconciliation; historical success never recreates.
    from .document_creation_binding import owned

    bound = owned(db, client_id, task_id)
    row = db.execute(
        "SELECT * FROM document_creation_saves WHERE run_id=?", (bound["run_id"],)
    ).fetchone()
    if row is None:
        raise InvalidInput("document_save_not_prepared")
    saved = _checked(row)
    vault = vaults.checked_vault(config, auth.grant(db, client_id, row["vault_id"], "add"))
    if row["state"] == "published":
        for source in saved["source_vault_ids"]:
            auth.grant(db, client_id, source, "query")
        return {
            "run_id": row["run_id"],
            "state": "published",
            "historical": True,
            "relative_path": saved["relative_path"],
            "knowledge_verified": False,
        }
    raw, metadata = artifacts.get(config, db, client_id, task_id)
    current, files = preview(config, db, client_id, task_id, row["vault_id"])
    if canonical(current) != row["intent_json"]:
        raise InvalidInput("document_save_scope_changed")
    approval = canonical(
        {
            "intent_sha256": row["intent_sha256"],
            "readers": saved["readers"],
            "expansion_readers": saved["expansion_readers"],
        }
    )
    if saved["expansion_readers"] and row["approval_json"] != approval:
        raise InvalidInput("document_save_scope_approval_required")
    if row["state"] == "conflict":
        raise IntegrityError("document_save_conflict; preserve files for review")
    # The immutable draft and provenance are also archived as ordinary explicit knowledge assets.
    receipt = Store(config).ingest_locked(
        db,
        ingress_scope="document-analysis",
        event_id=row["run_id"],
        vault=auth.grant(db, client_id, row["vault_id"], "add")["name"],
        source_key="document-analysis:" + row["run_id"],
        raw_event=canonical({"run_id": row["run_id"]}).encode(),
        items=(
            AssetInput(
                raw, metadata["artifact"]["original_name"], metadata["artifact"]["media_type"]
            ),
            AssetInput(files["provenance.json"], "provenance.json", "application/json"),
        ),
        session_scope="document-analysis",
        session_id=task_id,
        retention="knowledge",
        owner_client_id=client_id,
    )
    if receipt.state != "stored":
        raise InvalidInput("document_save_archive_not_stored")
    db.execute(
        "UPDATE document_creation_saves SET source_version_id=? WHERE run_id=?",
        (receipt.source_version_id, row["run_id"]),
    )
    stage, target = saved["staging_path"], saved["relative_path"]
    if row["state"] == "prepared":
        if contained(vault, stage).exists() or contained(vault, target).exists():
            db.execute(
                "UPDATE document_creation_saves SET state='conflict' WHERE run_id=?",
                (row["run_id"],),
            )
            raise IntegrityError("document_save_path_exists")
        db.execute(
            "UPDATE document_creation_saves SET state='publishing' WHERE run_id=?", (row["run_id"],)
        )
        publication_files.stage(vault, stage, files)
    if contained(vault, target).exists():
        if contained(vault, stage).exists():
            raise IntegrityError("document_save_ambiguous_effect")
        publication_files.verify(vault, target, files)
    else:
        publication_files.verify(vault, stage, files)
        publication_files.rename_exclusive(vault, stage, target)
        publication_files.verify(vault, target, files)
    if [vault.stat().st_dev, vault.stat().st_ino] != saved["vault_key"]:
        raise IntegrityError("document_save_vault_changed")
    db.execute(
        "UPDATE document_creation_saves SET state='published' WHERE run_id=?", (row["run_id"],)
    )
    return {
        "run_id": row["run_id"],
        "state": "published",
        "relative_path": target,
        "source_version_id": receipt.source_version_id,
        "knowledge_verified": False,
    }


def search_source(
    db: sqlite3.Connection, client_id: str, vault_id: str, relative_path: str, sha256: str
) -> tuple[str, str] | None:
    row = db.execute(
        "SELECT * FROM document_creation_saves WHERE vault_id=? AND state='published'", (vault_id,)
    ).fetchall()
    for saved in row:
        value = _checked(saved)
        if value["relative_path"] + "/note.md" != relative_path:
            continue
        # Grants added later do not silently expand disclosure. Approval binds a fixed audience.
        if client_id not in value["readers"]:
            raise auth.Denied(404)
        for source in value["source_vault_ids"]:
            auth.grant(db, value["client_id"], source, "query")
            if client_id not in value["expansion_readers"]:
                auth.grant(db, client_id, source, "query")
        kind = (
            "agent_analysis" if sha256 == value["files"]["note.md"]["sha256"] else "user_revision"
        )
        return kind, "已有 Agent 分析，非独立证据；关键事实须回查原始来源"
    return None


def inventory_paths(db: sqlite3.Connection, vault_id: str) -> tuple[set[str], set[str]]:
    rows = db.execute(
        "SELECT * FROM document_creation_saves WHERE vault_id=?", (vault_id,)
    ).fetchall()
    values = [(row["state"], _checked(row)["relative_path"]) for row in rows]
    return {p for _, p in values}, {p for state, p in values if state == "published"}


def archive_visible(db: sqlite3.Connection, client_id: str, version_id: str) -> bool:
    """Guard archived drafts/provenance too, including a crash before linking the saved version."""
    from .vault_merge_guard import original_visible

    if not original_visible(db, version_id):
        return False
    receipt = db.execute(
        "SELECT event_id FROM receipts WHERE source_version_id=? "
        "AND ingress_scope='document-analysis'",
        (version_id,),
    ).fetchone()
    if receipt is None:
        return True
    row = db.execute(
        "SELECT * FROM document_creation_saves WHERE run_id=?", (receipt[0],)
    ).fetchone()
    if row is None:
        return False
    value = _checked(row)
    if client_id != value["client_id"] and (
        row["state"] != "published" or client_id not in value["readers"]
    ):
        return False
    try:
        for source in value["source_vault_ids"]:
            auth.grant(db, value["client_id"], source, "query")
            if client_id not in value["expansion_readers"]:
                auth.grant(db, client_id, source, "query")
    except auth.Denied:
        return False
    return True
