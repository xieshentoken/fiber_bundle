"""Frozen processing plans in existing source bindings; old rows stay untouched."""

import json
import sqlite3
from typing import Any

from . import source_processing_contract as contract
from .config import IntegrityError
from .inputs import canonical

REQUEST_FIELDS = {
    "client_id",
    "vault_id",
    "receipt_id",
    "asset_id",
    "source_sha256",
    "kind",
    "submission_id",
    "configuration_sha256",
    "model_configuration_sha256",
}


def _parse(raw: str) -> dict[str, Any]:
    try:
        if not isinstance(raw, str) or len(raw.encode()) > 16384:
            raise ValueError()
        value = json.loads(raw)
        if not isinstance(value, dict) or canonical(value) != raw:
            raise ValueError()
        return value
    except (ValueError, UnicodeError):
        raise IntegrityError("source submission invalid") from None


def envelope(request: dict[str, Any], plan: dict[str, Any]) -> str:
    return canonical({"schema": "source-submission/v2", "request": request, "processing": plan})


def submission(raw: str) -> dict[str, Any]:
    value = _parse(raw)
    if "schema" not in value:
        if set(value) != REQUEST_FIELDS:
            raise IntegrityError("source submission invalid")
        return value
    if (
        set(value) != {"schema", "request", "processing"}
        or value["schema"] != "source-submission/v2"
    ):
        raise IntegrityError("source submission invalid")
    if not isinstance(value["request"], dict) or set(value["request"]) != REQUEST_FIELDS:
        raise IntegrityError("source submission invalid")
    contract.validate_plan(value["processing"])
    return value["request"]


def frozen(db: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    row = db.execute("SELECT * FROM source_task_bindings WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        return None
    task = db.execute(
        "SELECT d.*,COALESCE(p.format,'pdf') AS format,COALESCE(p.profile,'native') AS profile "
        "FROM document_tasks d LEFT JOIN document_profiles p ON p.run_id=d.run_id WHERE d.run_id=?",
        (run_id,),
    ).fetchone()
    if task is None:  # Existing article tasks keep their separate capture contract.
        return None
    request = submission(row["request_json"])
    if any(
        request[k] != row[column]
        for k, column in (
            ("client_id", "client_id"),
            ("vault_id", "vault_id"),
            ("receipt_id", "parent_receipt_id"),
            ("asset_id", "parent_asset_id"),
            ("source_sha256", "parent_sha256"),
            ("submission_id", "submission_id"),
        )
    ):
        raise IntegrityError("source submission binding changed")
    value = _parse(row["request_json"])
    if "schema" in value:
        submission(row["request_json"])
        plan = contract.validate_plan(value["processing"])
    else:
        # No backfill: derive the old capacity and image from immutable task columns.
        plan = contract.freeze(
            {
                "document_image": task["worker_image"],
                "article_image": task["worker_image"],
                "timeout_seconds": task["worker_timeout"],
            },
            format=task["format"],
            profile=task["profile"],
            source_sha256=task["source_sha256"],
            source_bytes=task["source_bytes"],
        )
    effective_format = "text" if plan["format"] == "markdown" else plan["format"]
    if (
        (effective_format, plan["profile"], plan["source_sha256"], plan["source_bytes"])
        != (task["format"], task["profile"], task["source_sha256"], task["source_bytes"])
        or plan["action"] != "process"
        or plan["engine"] != "ordinary"
        or contract.worker_binding(plan)[0] != task["worker_image"]
        or plan["policy"]["timeout_seconds"] != task["worker_timeout"]
    ):
        raise IntegrityError("source processing binding changed")
    return plan
