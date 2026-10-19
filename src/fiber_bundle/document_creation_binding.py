"""Execution binding checks and cancellation, without source/body access or model calls."""

import json
import re
import sqlite3
import time
from datetime import date
from typing import Any
from uuid import UUID

from . import auth, database
from . import document_creation_runtime_state as runtime
from .answer_model_output import reject_constant, unique_object
from .config import IntegrityError, InvalidInput
from .document_creation_config import MAX_BYTES, CreationExecutionConfig, parse
from .inputs import canonical
from .objects import digest


def get(db: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
    row = db.execute("SELECT * FROM document_creation_runs WHERE task_id=?", (task_id,)).fetchone()
    return row if isinstance(row, sqlite3.Row) else None


def owned(db: sqlite3.Connection, client_id: str, task_id: str) -> sqlite3.Row:
    active = db.execute("SELECT active FROM clients WHERE id=?", (client_id,)).fetchone()
    if active is None or active[0] != 1:
        raise auth.Denied(401)
    bound = get(db, task_id)
    if bound is None or bound["client_id"] != client_id:
        raise auth.Denied(404)
    return bound


def checked(
    db: sqlite3.Connection, bound: sqlite3.Row
) -> tuple[sqlite3.Row, CreationExecutionConfig]:
    try:
        if len(bound["configuration_json"].encode()) > MAX_BYTES or any(
            re.fullmatch(r"[a-f0-9]{64}", bound[k]) is None
            for k in ("plan_sha256", "input_sha256", "configuration_sha256", "request_sha256")
        ):
            raise ValueError()
        policy = parse(
            json.loads(
                bound["configuration_json"],
                object_pairs_hook=unique_object,
                parse_constant=reject_constant,
            )
        )
        model = policy.policy()
        run = db.execute("SELECT * FROM execution_runs WHERE id=?", (bound["run_id"],)).fetchone()
        if len(bound["request_json"].encode()) > model.max_request_bytes:
            raise ValueError()
        payload = json.loads(
            bound["request_json"], object_pairs_hook=unique_object, parse_constant=reject_constant
        )
        if (
            not isinstance(run, sqlite3.Row)
            or canonical(policy.value()) != bound["configuration_json"]
            or policy.sha256 != bound["configuration_sha256"]
            or run["config_digest"] != model.digest
            or run["client_id"] != bound["client_id"]
            or run["binding_kind"] != "notes"
            or run["version_id"] is not None
            or run["task_limit_nusd"] != model.task_limit_nusd
            or run["currency"] != "USD"
            or run["attempts"] != 1
            or run["state"] not in {"running", "failed", "cancelled", "interrupted", "succeeded"}
            or str(UUID(bound["owner_id"])) != bound["owner_id"]
            or canonical(payload) != bound["request_json"]
            or digest(bound["request_json"].encode()) != bound["request_sha256"]
        ):
            raise ValueError()
        calls = db.execute("SELECT * FROM model_calls WHERE run_id=?", (run["id"],)).fetchall()
        runtime.checked(db, bound, policy, calls)
        for call in calls:
            if any(
                call[k] != v
                for k, v in {
                    "model": model.model,
                    "price_revision": model.price_revision,
                    "reserved_nusd": model.reserve_nusd,
                    "currency": "USD",
                }.items()
            ):
                raise ValueError()
            if date.fromisoformat(call["billing_day"]).isoformat() != call["billing_day"]:
                raise ValueError()
            if call["state"] == "settled":
                if any(
                    type(call[k]) is not int or call[k] < 0
                    for k in ("input_tokens", "output_tokens")
                ):
                    raise ValueError()
                expected_cost = (
                    None
                    if model.price_revision == "unknown"
                    else call["input_tokens"] * model.input_nusd
                    + call["output_tokens"] * model.output_nusd
                )
                if call["charged_nusd"] != expected_cost:
                    raise ValueError()
            elif any(
                call[k] is not None for k in ("charged_nusd", "input_tokens", "output_tokens")
            ):
                raise ValueError()
            if run["state"] != "running" and call["state"] == "reserved":
                raise ValueError()
        artifact = db.execute(
            "SELECT 1 FROM document_creation_artifacts WHERE run_id=?", (run["id"],)
        ).fetchone()
        if (run["state"] == "succeeded") != (artifact is not None):
            raise ValueError()
        return run, policy
    except (KeyError, TypeError, ValueError, RecursionError, InvalidInput):
        raise IntegrityError("document_execution_binding_invalid") from None


def report(db: sqlite3.Connection, bound: sqlite3.Row) -> dict[str, Any]:
    run, _ = checked(db, bound)
    call = db.execute("SELECT * FROM model_calls WHERE run_id=?", (run["id"],)).fetchone()
    result = {
        "run_id": run["id"],
        "state": run["state"],
        "error_code": run["error_code"],
        "revision": bound["revision"],
        "plan_sha256": bound["plan_sha256"],
        "configuration_sha256": bound["configuration_sha256"],
        "request_sha256": bound["request_sha256"],
        "model_call": {
            k: call[k]
            for k in ("state", "reserved_nusd", "charged_nusd", "input_tokens", "output_tokens")
        },
        "execution_allowed": False,
        "delivery_allowed": False,
        "save_allowed": False,
        "runtime_verified": False,
        "request_limits_checked": True,
    }

    summary = runtime.summary(db, run["id"])
    if summary is not None:
        result["runtime"] = summary
        result["model_calls"] = [
            {
                k: row[k]
                for k in ("state", "reserved_nusd", "charged_nusd", "input_tokens", "output_tokens")
            }
            for row in db.execute(
                "SELECT * FROM model_calls WHERE run_id=? ORDER BY rowid", (run["id"],)
            )
        ]
        artifact = db.execute(
            "SELECT artifact_json FROM document_creation_artifacts WHERE run_id=?", (run["id"],)
        ).fetchone()
        if artifact is not None:
            result["draft_version"] = json.loads(artifact[0])
    return result


def stop(db: sqlite3.Connection, bound: sqlite3.Row, *, interrupt: bool = False) -> None:
    """Commit the stop before a separate sidecar cancellation; never release its fees."""
    run, _ = checked(db, bound)
    if run["state"] != "running":
        return
    with database.transaction(db):
        db.execute(
            "UPDATE model_calls SET state='uncertain' WHERE run_id=? AND state='reserved'",
            (run["id"],),
        )
        db.execute(
            "UPDATE execution_runs SET state=?,error_code=?,updated_at=? "
            "WHERE id=? AND state='running'",
            (
                "interrupted" if interrupt else "cancelled",
                "document_interrupted" if interrupt else "document_cancelled",
                time.time(),
                run["id"],
            ),
        )
