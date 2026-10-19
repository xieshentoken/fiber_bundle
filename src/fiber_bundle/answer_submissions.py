"""Client-owned, idempotent answer submissions. Caller holds the storage lock."""

import json
import sqlite3
from typing import Any

from . import answer_scope, answer_tasks, auth, execution_ledger
from .config import Config, InvalidInput
from .execution_config import ExecutionConfig
from .inputs import text


class SubmissionConflict(InvalidInput):
    pass


def status(config: Config, db: sqlite3.Connection, client_id: str, run_id: str) -> dict[str, Any]:
    run = db.execute(
        "SELECT * FROM execution_runs WHERE id=? AND client_id=?", (run_id, client_id)
    ).fetchone()
    task = answer_tasks.get(db, run_id)
    if run is None or task is None:
        raise auth.Denied(404)
    answer_tasks.authorize(config, db, run, task)
    result = {
        "run_id": run_id,
        "state": run["state"],
        "error_code": run["error_code"],
        "result_url": f"/v1/answers/{run_id}" if run["state"] == "succeeded" else None,
    }

    request = json.loads(task["request_json"])
    if request.get("mode") == "cited_draft":
        result["mode"] = "cited_draft"
    if "scope_request" in request:
        result["vault_ids"] = answer_scope.stored(run, request)
        result["scope_mode"] = "default" if request["scope_request"] is None else "explicit"
    return result


def submit(
    config: Config,
    db: sqlite3.Connection,
    policy: ExecutionConfig,
    client_id: str,
    data: answer_tasks.AnswerInput
    | answer_tasks.NoteAnswerInput
    | answer_tasks.ScopedNoteAnswerInput,
) -> dict[str, Any]:
    if data.submission_id is None:
        raise InvalidInput("submission id required")
    text(data.submission_id, limit=400)
    if not isinstance(data, answer_tasks.ScopedNoteAnswerInput):
        answer_tasks.scope(config, db, client_id, data)
    receipt_id = data.receipt_id if isinstance(data, answer_tasks.AnswerInput) else None
    input_kind = "receipt" if isinstance(data, answer_tasks.AnswerInput) else "notes"
    previous = db.execute(
        "SELECT t.*,r.config_digest,r.vault_id FROM answer_tasks t "
        "JOIN execution_runs r ON r.id=t.run_id "
        "WHERE r.client_id=? AND json_extract(t.request_json,'$.submission_id')=?",
        (client_id, data.submission_id),
    ).fetchall()
    request = answer_tasks.request_data(data)
    scoped = isinstance(data, answer_tasks.ScopedNoteAnswerInput)
    if previous:
        stored_request = json.loads(previous[0]["request_json"])
        if scoped:
            stored_request.pop("vault_ids", None)
        if (
            len(previous) != 1
            or previous[0]["receipt_id"] != receipt_id
            or previous[0]["input_kind"] != input_kind
            or (
                isinstance(data, answer_tasks.NoteAnswerInput)
                and previous[0]["vault_id"] != data.vault_id
            )
            or (stored_request != request or previous[0]["config_digest"] != policy.digest)
        ):
            raise SubmissionConflict("submission id already used for different input")
        return status(config, db, client_id, previous[0]["run_id"])
    vault_id, version_id = answer_tasks.scope(config, db, client_id, data)
    run_id = execution_ledger.create_run(
        config, db, policy, client_id, vault_id, version_id, answer=data
    )
    return status(config, db, client_id, run_id)
