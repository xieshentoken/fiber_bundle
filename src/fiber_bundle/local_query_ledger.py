"""Atomic local attempt ownership and non-monetary limits; no network or auto replay.

Callers hold the storage lock. Claim commits before any provider call. Completion
reauthorizes the frozen evidence, retaining reported usage even if results are denied.
"""

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from . import (
    answer_generation,
    auth,
    database,
    local_query_budget,
    local_query_contract,
    local_query_tasks,
)
from .config import Config, InvalidInput, StorageError
from .local_query_gateway import REASONS
from .query_routing_config import QueryRoutingConfig


class LocalLimitExceeded(InvalidInput):
    pass


@dataclass(frozen=True)
class Claim:
    task_id: str
    call_id: str
    client_id: str
    configuration_sha256: str
    request: dict[str, Any]


def _day() -> str:
    return datetime.now(UTC).date().isoformat()


def _metadata(task: sqlite3.Row) -> dict[str, Any]:
    return {"task_id": task["id"], "state": task["state"], "error_code": task["error_code"]}


def _call(db: sqlite3.Connection, policy: QueryRoutingConfig, claim: Claim) -> sqlite3.Row:
    if claim.configuration_sha256 != policy.digest:
        raise InvalidInput("local_configuration_changed")
    row = db.execute(
        "SELECT * FROM local_query_calls WHERE id=? AND task_id=? AND config_digest=?",
        (claim.call_id, claim.task_id, policy.digest),
    ).fetchone()
    task = local_query_tasks.get(db, claim.client_id, claim.task_id)
    if not isinstance(row, sqlite3.Row) or task["config_digest"] != policy.digest:
        raise InvalidInput("local_claim_invalid")
    return row


def claim(
    config: Config,
    db: sqlite3.Connection,
    policy: QueryRoutingConfig,
    client_id: str,
    task_id: str,
) -> Claim:
    local = local_query_tasks.local_policy(policy)
    with database.transaction(db):
        task = local_query_tasks.get(db, client_id, task_id)
        if task["config_digest"] != policy.digest:
            raise InvalidInput("local_configuration_changed")
        if (
            task["state"] != "queued"
            or db.execute("SELECT 1 FROM local_query_calls WHERE task_id=?", (task_id,)).fetchone()
        ):
            raise InvalidInput("local_attempt_not_replayable")
        response = local_query_tasks.current(config, db, task)
        request = answer_generation.model_request(json.loads(task["request_json"]), response)
        local_query_contract.prepare(local, request)
        day = _day()
        count = local_query_budget.count(db, day)
        # One reserved Agent turn or built-in call consumes a shared slot.
        if count >= local.daily_calls:
            raise LocalLimitExceeded("local_daily_call_limit")
        call_id = str(uuid4())
        now = time.time()
        db.execute(
            "INSERT INTO local_query_calls "
            "(id,task_id,config_digest,billing_day,state,created_at) VALUES (?,?,?,?,'reserved',?)",
            (call_id, task_id, policy.digest, day, now),
        )
        db.execute(
            "UPDATE local_query_tasks SET state='running',updated_at=? WHERE id=?", (now, task_id)
        )
        return Claim(task_id, call_id, client_id, policy.digest, request)


def finish(
    config: Config,
    db: sqlite3.Connection,
    policy: QueryRoutingConfig,
    owned: Claim,
    body: object,
    *,
    execution_succeeded: bool = True,
) -> dict[str, Any]:
    if type(execution_succeeded) is not bool:
        raise InvalidInput("local_execution_result_invalid")
    local = local_query_tasks.local_policy(policy)
    try:
        completion = local_query_contract.accept(local, body)
    except (StorageError, UnicodeError, ValueError, RecursionError):
        return fail(config, db, policy, owned, "local_response_invalid")
    with database.transaction(db):
        call = _call(db, policy, owned)
        task = local_query_tasks.get(db, owned.client_id, owned.task_id)
        if not (
            (task["state"] == "running" and call["state"] == "reserved")
            or (task["state"] == "cancelled" and call["state"] == "uncertain")
        ):
            raise InvalidInput("local_completion_not_active")
        usage = completion["usage"]
        now = time.time()
        db.execute(
            "UPDATE local_query_calls SET state='settled',input_tokens=?,output_tokens=?,"
            "completed_at=? WHERE id=?",
            (usage["prompt_tokens"], usage["completion_tokens"], now, owned.call_id),
        )
        if task["state"] == "cancelled":
            return _metadata(task)
        if not execution_succeeded:
            db.execute(
                "UPDATE local_query_tasks SET state='failed',error_code='local_executor_failed',"
                "updated_at=? WHERE id=?",
                (now, owned.task_id),
            )
            return _metadata(local_query_tasks.get(db, owned.client_id, owned.task_id))
        try:
            response = local_query_tasks.current(config, db, task)
            result = answer_generation.accept(
                json.loads(task["request_json"]), completion, response["sources"]
            )
        except (auth.Denied, StorageError, OSError, ValueError):
            db.execute(
                "UPDATE local_query_tasks SET state='failed',error_code='local_result_rejected',"
                "updated_at=? WHERE id=?",
                (now, owned.task_id),
            )
        else:
            db.execute(
                "UPDATE local_query_tasks SET state='succeeded',result_json=?,error_code=NULL,"
                "updated_at=? WHERE id=?",
                (json.dumps(result), now, owned.task_id),
            )
        return _metadata(local_query_tasks.get(db, owned.client_id, owned.task_id))


def fail(
    config: Config,
    db: sqlite3.Connection,
    policy: QueryRoutingConfig,
    owned: Claim,
    reason: str,
) -> dict[str, Any]:
    local_query_tasks.local_policy(policy)
    if not isinstance(reason, str) or reason not in REASONS | {"local_executor_failed"}:
        raise InvalidInput("local_failure_code_invalid")
    with database.transaction(db):
        call = _call(db, policy, owned)
        task = local_query_tasks.get(db, owned.client_id, owned.task_id)
        if not (
            (task["state"] == "running" and call["state"] == "reserved")
            or (task["state"] == "cancelled" and call["state"] == "uncertain")
        ):
            raise InvalidInput("local_completion_not_active")
        db.execute("UPDATE local_query_calls SET state='uncertain' WHERE id=?", (owned.call_id,))
        if task["state"] == "running":
            db.execute(
                "UPDATE local_query_tasks SET state='failed',error_code=?,updated_at=? WHERE id=?",
                (reason, time.time(), owned.task_id),
            )
        return _metadata(local_query_tasks.get(db, owned.client_id, owned.task_id))


def stop(
    config: Config, db: sqlite3.Connection, client_id: str, task_id: str, *, interrupt: bool = False
) -> dict[str, Any]:
    """Explicit cancellation/recovery. No process stopping is claimed by this core API."""
    if type(interrupt) is not bool:
        raise InvalidInput("local_stop_mode_invalid")
    with database.transaction(db):
        task = local_query_tasks.get(db, client_id, task_id)
        # Recovery may still be needed after scope withdrawal; ownership stays exact.
        client = db.execute("SELECT active FROM clients WHERE id=?", (client_id,)).fetchone()
        if client is None or not client[0]:
            raise auth.Denied(401)
        if task["state"] not in ("queued", "running") or (interrupt and task["state"] != "running"):
            return _metadata(task)
        db.execute(
            "UPDATE local_query_calls SET state='uncertain' WHERE task_id=? AND state='reserved'",
            (task_id,),
        )
        state = "interrupted" if interrupt else "cancelled"
        db.execute(
            "UPDATE local_query_tasks SET state=?,error_code=?,updated_at=? WHERE id=?",
            (state, "local_" + state, time.time(), task_id),
        )
        return _metadata(local_query_tasks.get(db, client_id, task_id))
