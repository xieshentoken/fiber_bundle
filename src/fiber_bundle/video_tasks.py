"""Durable video task admission, current authorization and explicit user actions."""

import json
import sqlite3
import time
from typing import Any
from uuid import uuid4

from . import auth, automatic_policy, objects, source_imports, video_contract, video_tools
from .config import Config, InvalidInput, contained
from .document_source import DocumentFile
from .executor_container import run_lock
from .inputs import canonical
from .media_subtitles import parse
from .query_policy_store import write_private
from .query_routing_files import read


def settings(config: Config) -> dict[str, Any]:
    path = contained(config.state_root, "configs/video-processing.json")
    value = (
        video_contract.Settings.model_validate_json(read(path, 32768)).model_dump()
        if (path.exists())
        else video_contract.Settings().model_dump()
    )
    return {"settings": value, "sha256": objects.digest(canonical(value).encode())}


def configure(config: Config, value: video_contract.Settings, expected: str) -> dict[str, Any]:
    if settings(config)["sha256"] != expected:
        raise InvalidInput("video_settings_changed")
    if value.asr_resources:
        from .media_asr_runtime import require
        from .media_processing_contract import policy

        require(policy(value.asr_resources))
        if value.image != value.asr_resources["image"]:
            raise InvalidInput("video_asr_image_mismatch")
    from .config import make_directory

    make_directory(config.state_root / "configs")
    write_private(
        config.state_root / "configs/video-processing.json", canonical(value.model_dump()).encode()
    )
    return settings(config)


def source(config: Config, db: sqlite3.Connection, row: sqlite3.Row) -> tuple[DocumentFile, str]:
    imported = source_imports.get(config, db, row["client_id"], row["import_id"])
    plan = json.loads(imported["plan_json"])
    if imported["state"] != "archived" or plan["format"] not in {"video", "audio"}:
        raise InvalidInput("video_archived_source_required")
    from .store import Store

    Store(config).verify_receipt_locked(db, imported["receipt_id"])
    asset = db.execute(
        "SELECT * FROM assets WHERE id=? AND vault_id=?", (imported["asset_id"], row["vault_id"])
    ).fetchone()
    if asset is None or asset["sha256"] != plan["source_sha256"]:
        raise InvalidInput("video_source_changed")
    relative = objects.object_relative(row["vault_id"], asset["sha256"])
    if asset["relative_path"] != relative or asset["byte_count"] > video_contract.MAX_SOURCE_BYTES:
        raise InvalidInput("video_source_capacity")
    version = db.execute(
        "SELECT source_version_id FROM receipts WHERE job_id=?", (imported["receipt_id"],)
    ).fetchone()[0]
    return DocumentFile(
        contained(config.state_root, relative), asset["sha256"], asset["byte_count"]
    ), version


def get(config: Config, db: sqlite3.Connection, cid: str, identity: str) -> sqlite3.Row:
    row = db.execute(
        "SELECT * FROM video_tasks WHERE id=? AND client_id=?", (identity, cid)
    ).fetchone()
    if not isinstance(row, sqlite3.Row):
        raise auth.Denied(404)
    source(config, db, row)
    policy = json.loads(row["policy_json"])
    if source_imports.epoch(db, cid, row["vault_id"]) != policy["permission_epoch"]:
        raise InvalidInput("video_permission_changed")
    if automatic_policy.get(db, row["vault_id"]) != policy["vault_policy"]:
        raise InvalidInput("video_processing_policy_changed")
    if (
        policy.get("model_configuration_sha256")
        and automatic_policy.processing_route(config, policy["vault_policy"]["settings"]).digest
        != policy["model_configuration_sha256"]
    ):
        raise InvalidInput("video_processing_policy_changed")
    if video_tools.code_digest() != policy["tool_sha256"]:
        raise InvalidInput("video_tools_changed")
    return row


def project(row: sqlite3.Row) -> dict[str, Any]:
    value = json.loads(row["value"])
    from .processing_errors import MESSAGES

    return {
        "error_message": MESSAGES.get(value.get("error_code")),
        "id": row["id"],
        "client_id": row["client_id"],
        "vault_id": row["vault_id"],
        "import_id": row["import_id"],
        "state": row["state"],
        **value,
        "model_cost_scope": "Fiber calls only; external host costs unknown",
    }


def update(db: sqlite3.Connection, identity: str, state: str, value: dict[str, Any]) -> None:
    db.execute(
        "UPDATE video_tasks SET state=?,value=?,updated_at=? WHERE id=?",
        (state, canonical(value), time.time(), identity),
    )


def summary(row: sqlite3.Row) -> dict[str, Any]:
    """Task lists carry status only; captured frame/transcript bytes stay out of home reads."""
    item = project(row)
    return {
        k: item[k]
        for k in (
            "id",
            "client_id",
            "vault_id",
            "import_id",
            "state",
            "error_message",
            "error_code",
            "completeness_status",
            "known_gaps",
            "model_cost_scope",
        )
        if k in item
    } | {
        "segments": [
            {k: segment[k] for k in ("start_ms", "end_ms", "state", "error_code") if k in segment}
            for segment in item.get("segments", [])
        ]
    }


def create(
    config: Config, db: sqlite3.Connection, request: video_contract.Create
) -> dict[str, Any]:
    existing = db.execute(
        "SELECT id FROM video_tasks WHERE import_id=? AND client_id=?",
        (request.import_id, request.client_id),
    ).fetchone()
    if existing:
        return project(get(config, db, request.client_id, existing[0]))
    imported = source_imports.get(config, db, request.client_id, request.import_id)
    resources = settings(config)["settings"]
    if not resources["image"]:
        raise InvalidInput("video_runtime_not_configured")
    policy = automatic_policy.get(db, imported["vault_id"])
    if (
        automatic_policy.active(policy["settings"])
        and policy["settings"]["client_id"] != request.client_id
    ):
        raise InvalidInput("video_policy_identity_mismatch")
    cues = (
        parse(
            request.subtitle.encode(), request.subtitle_kind, request.duration_ms, long_video=True
        )
        if request.subtitle
        else []
    )
    glossary = None
    if not request.subtitle:
        from .media_asr_runtime import require

        if not resources["asr_resources"]:
            raise InvalidInput("video_transcription_capability_missing")
        require(resources["asr_resources"])
        if request.glossary is None:
            from .video_skill import native_glossary

            glossary = native_glossary(config)
        else:
            glossary = video_contract.confirmed_glossary(
                request.glossary, request.glossary_confirmed
            )
    frozen = {
        "vault_policy": policy,
        "model_configuration_sha256": automatic_policy.processing_route(
            config, policy["settings"]
        ).digest
        if policy["settings"].get("processing_mode") == "pi_skill"
        else None,
        "resources": resources,
        "permission_epoch": source_imports.epoch(db, request.client_id, imported["vault_id"]),
        "tool_sha256": video_tools.code_digest(),
        "glossary": glossary,
        "terminology_adoption": "application_policy" if request.glossary is None else "explicit",
        "duration_ms": request.duration_ms,
        "frames": request.frames,
        "subtitle": bool(request.subtitle),
        "cues": cues,
        "format": "wav" if json.loads(imported["plan_json"])["format"] == "audio" else "mp4",
    }
    identity, now = str(uuid4()), time.time()
    value = {
        "segments": [
            {**span, "state": "pending"} for span in video_contract.segments(request.duration_ms)
        ],
        "known_gaps": [],
        "knowledge_verified": False,
        "completeness_status": "unknown",
    }
    db.execute(
        "INSERT INTO video_tasks VALUES(?,?,?,?,?,?,?,?,?)",
        (
            identity,
            request.client_id,
            imported["vault_id"],
            request.import_id,
            "queued",
            canonical(frozen),
            canonical(value),
            now,
            now,
        ),
    )
    row = get(config, db, request.client_id, identity)
    source(config, db, row)[0].verify()
    return project(row)


def action(
    config: Config, db: sqlite3.Connection, cid: str, identity: str, operation: str
) -> dict[str, Any]:
    row = get(config, db, cid, identity)
    value = json.loads(row["value"])
    if operation == "cancel":
        if row["state"] == "published":
            raise InvalidInput("video_already_published")
        update(db, identity, "cancelled", value)
    elif operation == "resume":
        if row["state"] not in {
            "cancelled",
            "failed",
            "waiting_budget",
            "interrupted",
            "running",
            "launching",
        }:
            raise InvalidInput("video_resume_not_required")
        # Completed segments and model run IDs are preserved. Unknown paid calls are not retried.
        with run_lock(config, identity):
            update(db, identity, "queued", value)
    elif operation == "publish":
        if row["state"] != "needs_confirmation":
            raise InvalidInput("video_artifact_not_ready")
        from .video_runner import publish

        publish(config, db, row, value, user_confirmed=True)
    else:
        raise InvalidInput("video_action_invalid")
    return project(get(config, db, cid, identity))
