"""Client-owned local answer intents, frozen scope and result delivery.

Callers hold the storage lock. No provider, credential or executor access here.
"""

import json
import sqlite3
import time
from typing import Any
from uuid import uuid4

from . import answer_generation, answer_scope, answer_tasks, auth, database, vaults
from .config import Config, IntegrityError, InvalidInput
from .inputs import text
from .query_routing_config import LocalQueryConfig, QueryRoutingConfig

AnswerInput = (
    answer_tasks.AnswerInput | answer_tasks.NoteAnswerInput | answer_tasks.ScopedNoteAnswerInput
)


def local_policy(policy: QueryRoutingConfig) -> LocalQueryConfig:
    policy.validate()
    if policy.local_query is None:
        raise InvalidInput("local_query_not_configured")
    return policy.local_query


def get(db: sqlite3.Connection, client_id: str, task_id: str) -> sqlite3.Row:
    row = db.execute(
        "SELECT *,id AS run_id FROM local_query_tasks WHERE id=? AND client_id=?",
        (task_id, client_id),
    ).fetchone()
    if not isinstance(row, sqlite3.Row):
        raise auth.Denied(404)
    return row


def current(config: Config, db: sqlite3.Connection, task: sqlite3.Row) -> dict[str, Any]:
    roots = json.loads(task["roots_json"])
    if roots != _roots(config, db, task):
        raise IntegrityError("local_vault_identity_changed")
    response = answer_tasks.current(config, db, task, task)
    if roots != _roots(config, db, task):
        raise IntegrityError("local_vault_identity_changed")
    return response


def active_check(
    config: Config,
    db: sqlite3.Connection,
    task: sqlite3.Row,
    *,
    call_id: str,
    configuration_sha256: str,
) -> bool:
    """Check cancellation, ownership and authority without retrieving note bodies.

    Content changes are checked by ``current`` before sending to the provider and
    after receiving its answer, before persistence and at delivery. During a call
    such changes do not stop computation; they can never authorize its result.
    """
    if task["state"] != "running":
        return False
    if (
        task["config_digest"] != configuration_sha256
        or not db.execute(
            "SELECT 1 FROM local_query_calls WHERE id=? AND task_id=? AND config_digest=? "
            "AND state='reserved'",
            (call_id, task["id"], configuration_sha256),
        ).fetchone()
    ):
        raise InvalidInput("local_claim_invalid")
    answer_tasks.authorize(config, db, task, task)
    if json.loads(task["roots_json"]) != _roots(config, db, task):
        raise IntegrityError("local_vault_identity_changed")
    return True


def _roots(config: Config, db: sqlite3.Connection, task: sqlite3.Row) -> list[dict[str, Any]]:
    result = []
    for vid in answer_scope.stored(task, json.loads(task["request_json"])):
        root = vaults.checked_vault(config, auth.grant(db, task["client_id"], vid, "query"))
        info = root.stat()
        result.append({"vault_id": vid, "device": info.st_dev, "inode": info.st_ino})
    return result


def status(config: Config, db: sqlite3.Connection, client_id: str, task_id: str) -> dict[str, Any]:
    task = get(db, client_id, task_id)
    answer_tasks.authorize(config, db, task, task)
    call = db.execute("SELECT * FROM local_query_calls WHERE task_id=?", (task_id,)).fetchone()
    return {
        "task_id": task_id,
        "state": task["state"],
        "error_code": task["error_code"],
        "configuration_sha256": task["config_digest"],
        "local_call": None
        if call is None
        else {
            "state": call["state"],
            "billing_day_utc": call["billing_day"],
            "input_tokens": call["input_tokens"],
            "output_tokens": call["output_tokens"],
        },
    }


def submit(
    config: Config,
    db: sqlite3.Connection,
    policy: QueryRoutingConfig,
    client_id: str,
    data: AnswerInput,
    *,
    task_id: str | None = None,
) -> dict[str, Any]:
    local_policy(policy)
    from .query_policy_store import freeze

    freeze(config, policy)
    if task_id is not None:
        from .executor_container import identifier

        identifier(task_id)
    text(data.submission_id, limit=400)
    request = answer_tasks.request_data(data)
    with database.transaction(db):
        previous = db.execute(
            "SELECT *,id AS run_id FROM local_query_tasks WHERE client_id=? AND submission_id=?",
            (client_id, data.submission_id),
        ).fetchone()
        if previous is not None:
            stored = json.loads(previous["request_json"])
            if isinstance(data, answer_tasks.ScopedNoteAnswerInput):
                stored.pop("vault_ids", None)
            receipt = data.receipt_id if isinstance(data, answer_tasks.AnswerInput) else None
            if (
                (task_id is not None and previous["id"] != task_id)
                or stored != request
                or previous["config_digest"] != policy.digest
                or previous["receipt_id"] != receipt
                or (
                    isinstance(data, answer_tasks.NoteAnswerInput)
                    and previous["vault_id"] != data.vault_id
                )
            ):
                raise InvalidInput("local_submission_conflict")
            return status(config, db, client_id, previous["id"])
        vault_id, version_id = answer_tasks.scope(config, db, client_id, data)
        if isinstance(data, answer_tasks.ScopedNoteAnswerInput):
            request["vault_ids"] = answer_scope.resolve(config, db, client_id, data.vault_ids)
        task_id = task_id or str(uuid4())
        receipt = data.receipt_id if isinstance(data, answer_tasks.AnswerInput) else None
        now = time.time()
        db.execute(
            "INSERT INTO local_query_tasks "
            "(id,client_id,submission_id,vault_id,version_id,binding_kind,receipt_id,input_kind,"
            "request_json,snapshot_json,roots_json,config_digest,state,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,'{}','[]',?,'queued',?,?)",
            (
                task_id,
                client_id,
                data.submission_id,
                vault_id,
                version_id,
                "source" if receipt is not None else "notes",
                receipt,
                "receipt" if receipt is not None else "notes",
                json.dumps(request),
                policy.digest,
                now,
                now,
            ),
        )
        task = get(db, client_id, task_id)
        roots = _roots(config, db, task)
        bound_request, snapshot = answer_tasks.prepare_binding(config, db, task, data)
        if bound_request != request or roots != _roots(config, db, task):
            raise IntegrityError("local_vault_identity_changed")
        # Reuse the binding's exact evidence snapshot from this transaction.
        # Claim rechecks it before a provider request can be created.
        db.execute(
            "UPDATE local_query_tasks SET snapshot_json=?,roots_json=? WHERE id=?",
            (json.dumps(snapshot), json.dumps(roots), task_id),
        )
        return status(config, db, client_id, task_id)


def deliver(config: Config, db: sqlite3.Connection, client_id: str, task_id: str) -> dict[str, Any]:
    task = get(db, client_id, task_id)
    response = current(config, db, task)
    if task["state"] != "succeeded" or task["result_json"] is None:
        raise InvalidInput("local_answer_not_ready")
    call = db.execute(
        "SELECT 1 FROM local_query_calls WHERE task_id=? AND config_digest=? AND state='settled'",
        (task_id, task["config_digest"]),
    ).fetchone()
    if call is None:
        raise InvalidInput("local_answer_not_ready")
    answer_generation.deliver(
        json.loads(task["request_json"]), json.loads(task["result_json"]), response
    )
    response.update(model_used=True, task_id=task_id, execution_role="local_query")
    return response
