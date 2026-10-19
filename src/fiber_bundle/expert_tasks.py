"""Full compiler admission and owner-bound task metadata; no parsing or model calls."""

import json
import sqlite3
import time
from typing import Any

from . import auth, document_bindings, expert_runtime, source_imports
from .config import Config, IntegrityError, InvalidInput
from .document_source import DocumentFile
from .inputs import canonical
from .objects import digest


def get(db: sqlite3.Connection, identity: str) -> sqlite3.Row | None:
    row = db.execute("SELECT * FROM source_expert_tasks WHERE id=?", (identity,)).fetchone()
    return row if isinstance(row, sqlite3.Row) else None


def authorize(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    identity: str,
    *,
    verify_file: bool = True,
) -> tuple[sqlite3.Row, dict[str, Any], DocumentFile]:
    source = source_imports.get(config, db, client_id, identity)
    plan = json.loads(source["plan_json"])
    if (
        source["state"] != "archived"
        or plan["engine"] != "fake-expert"
        or plan["action"] != "process"
        or plan["format"] != "pdf"
    ):
        raise InvalidInput("expert_source_not_prepared")
    runtime = plan["policy"].get("expert_runtime")
    expert_runtime.verify(runtime)
    task = get(db, identity)
    if task is not None and (task["request_sha256"], task["plan_sha256"]) != (
        digest(source["request_json"].encode()),
        plan["plan_sha256"],
    ):
        raise IntegrityError("expert_task_binding_changed")
    file = document_bindings.source(
        config, db, client_id, source["receipt_id"], source["asset_id"], verify_file=verify_file
    ).file
    return source, plan, file


def workpack(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    vault_id: str,
    identity: str | None,
    *,
    verify_file: bool = True,
) -> DocumentFile | None:
    if identity is None:
        return None
    imported = source_imports.get(config, db, client_id, identity)
    plan = json.loads(imported["plan_json"])
    if (
        imported["vault_id"] != vault_id
        or imported["state"] != "archived"
        or plan["action"] != "archive"
        or plan["format"] != "archive"
        or imported["run_id"]
    ):
        raise InvalidInput("expert_explicit_archived_workpack_required")
    return document_bindings.source(
        config, db, client_id, imported["receipt_id"], imported["asset_id"], verify_file=verify_file
    ).file


def prepare(config: Config, db: sqlite3.Connection, client_id: str, identity: str) -> str:
    source, plan, _ = authorize(config, db, client_id, identity)
    request = json.loads(source["request_json"])
    expert_runtime.selection(
        request.get("expert_profile", "native-text"), request.get("expert_backend")
    )
    workpack_id = request.get("workpack_import_id")
    workpack(config, db, client_id, source["vault_id"], workpack_id)
    if get(db, identity) is None:
        db.execute(
            "INSERT INTO "
            "source_expert_tasks(id,request_sha256,plan_sha256,state,"
            "workpack_import_id,updated_at) "
            "VALUES(?,?,?,'prepared',?,?)",
            (
                identity,
                digest(source["request_json"].encode()),
                plan["plan_sha256"],
                workpack_id,
                time.time(),
            ),
        )
    return identity


def projection(db: sqlite3.Connection, identity: str) -> dict[str, Any] | None:
    task = get(db, identity)
    if task is None:
        return None
    result = json.loads(task["result_json"]) if task["result_json"] else {}
    from .expert_authoring import projection as authoring_projection

    authoring = authoring_projection(db, identity)
    return {
        "authoring": authoring,
        "id": identity,
        "state": task["state"],
        "attempt": task["current_attempt"],
        "workpack_import_id": task["workpack_import_id"],
        "code": result.get("code"),
        "next_action": result.get("next_action"),
        "object_count": result.get("object_count", 0),
        "anchor_count": result.get("anchor_count", 0),
        "artifact_stage": "draft",
        "review_state": "deferred",
        "knowledge_verified": False,
        "compiler_model_calls": 0,
        "model_calls": db.execute(
            "SELECT count(*) FROM model_calls WHERE run_id IN (SELECT value "
            "FROM json_tree((SELECT payload FROM vault_operations WHERE id=?)) "
            "WHERE key='run_id')",
            (authoring["operation_id"] if authoring else "",),
        ).fetchone()[0],
    }


def cancel(db: sqlite3.Connection, client_id: str, identity: str) -> None:
    row = db.execute("SELECT client_id FROM source_imports WHERE id=?", (identity,)).fetchone()
    if row is None or row[0] != client_id:
        raise auth.Denied(404)
    db.execute(
        "UPDATE vault_operations SET state='cancelled' WHERE kind='expert_authoring' "
        "AND json_extract(payload,'$.import_id')=? AND state!='complete'",
        (identity,),
    )
    db.execute(
        "UPDATE source_expert_tasks SET state='cancelled',updated_at=? WHERE id=?",
        (time.time(), identity),
    )
    db.execute(
        "UPDATE source_expert_attempts SET state='cancelled' WHERE task_id=? AND state='running'",
        (identity,),
    )


def finish(
    db: sqlite3.Connection,
    identity: str,
    ordinal: int,
    state: str,
    result: dict[str, Any],
    result_sha: str | None,
) -> None:
    # Never overwrite a cancellation, revoke reconciliation, or newer explicit attempt.
    changed = db.execute(
        "UPDATE source_expert_tasks SET state=?,result_json=?,updated_at=? "
        "WHERE id=? AND current_attempt=? AND state='running'",
        (state, canonical(result), time.time(), identity, ordinal),
    ).rowcount
    if changed != 1:
        raise InvalidInput("expert_attempt_stopped")
    db.execute(
        "UPDATE source_expert_attempts SET state=?,result_sha256=? WHERE "
        "task_id=? AND ordinal=? AND state='running'",
        (state, result_sha, identity, ordinal),
    )
