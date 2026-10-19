"""Owned cloud editing intents, shared fee reservations and guarded delivery.

Callers hold the storage lock. There is no provider or credential access here.
"""

import sqlite3
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from . import auth, cloud_answer_edit, database, local_query_tasks
from . import cloud_answer_binding as binding
from . import cloud_answer_selection as selection
from . import execution_ledger as ledger
from .config import Config, IntegrityError, InvalidInput, StorageError
from .execution_config import ExecutionConfig
from .inputs import text
from .query_routing_config import QueryRoutingConfig

REASONS = frozenset(
    {
        "cloud_transport_failed",
        "cloud_response_rejected",
        "cloud_usage_rejected",
        "cloud_authority_changed",
        "cloud_timeout",
        "cloud_interrupted",
    }
)


@dataclass(frozen=True)
class Attempt:
    client_id: str
    run_id: str
    call_id: str
    routing_digest: str
    answer: dict[str, str]
    policy: ExecutionConfig


def get(db: sqlite3.Connection, client_id: str, run_id: str) -> tuple[sqlite3.Row, sqlite3.Row]:
    return binding.owned(db, client_id, run_id)


def status(db: sqlite3.Connection, client_id: str, run_id: str) -> dict[str, Any]:
    run, bound = get(db, client_id, run_id)
    active = db.execute("SELECT active FROM clients WHERE id=?", (client_id,)).fetchone()
    if active is None or not active[0]:
        raise auth.Denied(401)
    call = db.execute("SELECT * FROM model_calls WHERE run_id=?", (run_id,)).fetchone()
    return {
        "run_id": run_id,
        "local_task_id": bound["local_task_id"],
        "state": run["state"],
        "error_code": run["error_code"],
        "configuration_sha256": bound["routing_digest"],
        "automatic_switch": False,
        "selections": selection.report(db, client_id, run_id),
        "cloud_call": None
        if call is None
        else {k: call[k] for k in ("state", "reserved_nusd", "charged_nusd", "currency")},
    }


def submit(
    config: Config,
    db: sqlite3.Connection,
    route: QueryRoutingConfig,
    client_id: str,
    local_task_id: str,
    submission_id: str,
) -> dict[str, Any]:
    route.validate()
    policy = route.answer_policy()
    text(submission_id, limit=400)
    with database.transaction(db):
        prior = db.execute(
            "SELECT run_id,local_task_id,routing_digest FROM cloud_answer_tasks "
            "WHERE client_id=? AND submission_id=?",
            (client_id, submission_id),
        ).fetchone()
        if prior is not None:
            if prior["local_task_id"] != local_task_id or prior["routing_digest"] != route.digest:
                raise InvalidInput("cloud_submission_conflict")
            run, bound = get(db, client_id, prior["run_id"])
            binding.current(config, db, run, bound)
            return status(db, client_id, run["id"])
        task = local_query_tasks.get(db, client_id, local_task_id)
        if task["config_digest"] != route.digest:
            raise InvalidInput("cloud_configuration_changed")
        response = local_query_tasks.deliver(config, db, client_id, local_task_id)
        binding.prepare(route, binding.envelope(response))
        run_id = str(uuid4())
        now = time.time()
        db.execute(
            "INSERT INTO execution_runs "
            "(id,client_id,vault_id,version_id,config_digest,state,task_limit_nusd,currency,"
            "created_at,updated_at,binding_kind) VALUES (?,?,?,?,?,'queued',?,'USD',?,?,?)",
            (
                run_id,
                client_id,
                task["vault_id"],
                task["version_id"],
                policy.digest,
                policy.task_limit_nusd,
                now,
                now,
                task["binding_kind"],
            ),
        )
        db.execute(
            "INSERT INTO cloud_answer_tasks VALUES (?,?,?,?,?,?,NULL)",
            (
                run_id,
                client_id,
                submission_id,
                local_task_id,
                route.digest,
                binding.result_digest(task),
            ),
        )
        return status(db, client_id, run_id)


def current(
    config: Config,
    db: sqlite3.Connection,
    route: QueryRoutingConfig,
    client_id: str,
    run_id: str,
) -> dict[str, str]:
    route.validate()
    run, bound = get(db, client_id, run_id)
    selection.resolve(db, route, run, bound)
    return binding.envelope(binding.current(config, db, run, bound))


def claim(
    config: Config, db: sqlite3.Connection, route: QueryRoutingConfig, client_id: str, run_id: str
) -> Attempt:
    answer = current(config, db, route, client_id, run_id)
    run, bound = get(db, client_id, run_id)
    policy, index = selection.resolve(db, route, run, bound)
    binding.prepare(route, answer, backup_index=index)
    if run["state"] != "queued" or run["attempts"] != 0:
        raise InvalidInput("cloud_attempt_not_replayable")
    ledger.start(config, db, run_id, policy)
    try:
        call_id = ledger.reserve(config, db, run_id, policy, ceiling=route.answer_policy())
    except ledger.BudgetExceeded:
        ledger.transition(db, run_id, "failed", "cloud_budget_exceeded")
        raise
    except (KeyboardInterrupt, SystemExit):
        stop(db, client_id, run_id, interrupt=True)
        raise
    except BaseException:
        ledger.transition(db, run_id, "failed", "cloud_authority_changed")
        raise
    return Attempt(client_id, run_id, call_id, route.digest, answer, policy)


def _attempt(
    db: sqlite3.Connection, route: QueryRoutingConfig, owned: Attempt
) -> tuple[sqlite3.Row, sqlite3.Row]:
    route.validate()
    run, bound = get(db, owned.client_id, owned.run_id)
    policy, _ = selection.resolve(db, route, run, bound)
    call = db.execute(
        "SELECT * FROM model_calls WHERE id=? AND run_id=?", (owned.call_id, owned.run_id)
    ).fetchone()
    if (
        bound["routing_digest"] != route.digest
        or owned.routing_digest != route.digest
        or owned.policy != policy
        or call is None
        or call["model"] != policy.model
        or call["state"] not in ("reserved", "uncertain")
        or run["state"] not in ("running", "cancelled", "interrupted")
    ):
        raise InvalidInput("cloud_attempt_not_active")
    return run, bound


def fail(
    db: sqlite3.Connection, route: QueryRoutingConfig, owned: Attempt, reason: str
) -> dict[str, Any]:
    if not isinstance(reason, str) or reason not in REASONS:
        raise InvalidInput("cloud_failure_code_invalid")
    _attempt(db, route, owned)
    db.execute(
        "UPDATE model_calls SET state='uncertain' WHERE id=? AND state='reserved'", (owned.call_id,)
    )
    ledger.transition(
        db, owned.run_id, "interrupted" if reason == "cloud_interrupted" else "failed", reason
    )
    return _report(db, owned)


def _report(db: sqlite3.Connection, owned: Attempt) -> dict[str, Any]:
    # Internal completion can be audited even when the client was revoked in flight.
    run, _ = get(db, owned.client_id, owned.run_id)
    return {"run_id": owned.run_id, "state": run["state"], "error_code": run["error_code"]}


def finish(
    config: Config,
    db: sqlite3.Connection,
    route: QueryRoutingConfig,
    owned: Attempt,
    output: dict[str, Any],
) -> dict[str, Any]:
    run, _ = _attempt(db, route, owned)
    # Accounting precedes authority/output rejection; known usage must never roll back.
    try:
        ledger.settle(db, owned.call_id, owned.policy, output.get("usage"), reconcile=True)
    except InvalidInput:
        db.execute(
            "UPDATE model_calls SET state='uncertain' WHERE id=? AND state='reserved'",
            (owned.call_id,),
        )
        ledger.transition(db, owned.run_id, "failed", "cloud_usage_rejected")
        return _report(db, owned)
    if run["state"] in ("cancelled", "interrupted"):
        return _report(db, owned)
    try:
        if current(config, db, route, owned.client_id, owned.run_id) != owned.answer:
            raise IntegrityError("cloud_local_binding_changed")
    except (auth.Denied, StorageError):
        ledger.transition(db, owned.run_id, "failed", "cloud_authority_changed")
        return _report(db, owned)
    value = output.get("answer")
    try:
        if output.get("error_code") is not None or not isinstance(value, str) or not value.strip():
            raise InvalidInput("cloud_response_rejected")
        if len(value.encode()) > 16384:
            raise InvalidInput("cloud_response_rejected")
        value = cloud_answer_edit.accept(owned.answer, value)
    except (InvalidInput, UnicodeError):
        ledger.transition(db, owned.run_id, "failed", "cloud_response_rejected")
        return _report(db, owned)
    with database.transaction(db):
        db.execute(
            "UPDATE cloud_answer_tasks SET result_text=? WHERE run_id=?", (value, owned.run_id)
        )
        ledger.transition(db, owned.run_id, "succeeded")
    return _report(db, owned)


def stop(
    db: sqlite3.Connection, client_id: str, run_id: str, *, interrupt: bool = False
) -> dict[str, Any]:
    if type(interrupt) is not bool:
        raise InvalidInput("cloud_stop_mode_invalid")
    run, _ = get(db, client_id, run_id)
    if run["state"] in ("queued", "running"):
        if not interrupt or run["state"] == "running":
            db.execute(
                "UPDATE model_calls SET state='uncertain' WHERE run_id=? AND state='reserved'",
                (run_id,),
            )
            ledger.transition(
                db,
                run_id,
                "interrupted" if interrupt else "cancelled",
                "cloud_interrupted" if interrupt else "cancelled",
            )
    return {"run_id": run_id, "state": get(db, client_id, run_id)[0]["state"]}


def deliver(config: Config, db: sqlite3.Connection, client_id: str, run_id: str) -> dict[str, Any]:
    run, bound = get(db, client_id, run_id)
    response = binding.current(config, db, run, bound)
    call = db.execute("SELECT state FROM model_calls WHERE run_id=?", (run_id,)).fetchone()
    if (
        run["state"] != "succeeded"
        or bound["result_text"] is None
        or call is None
        or call[0] != "settled"
    ):
        raise InvalidInput("cloud_answer_not_ready")
    response.update(
        cloud_answer={"text": bound["result_text"], "semantic_verified": False},
        execution_role="cloud_answer",
        run_id=run_id,
        local_task_id=bound["local_task_id"],
    )
    response["cautions"].append("云端整理是未核验草稿；原始本地引文另列，不能视为云端断言的核验。")
    return response
