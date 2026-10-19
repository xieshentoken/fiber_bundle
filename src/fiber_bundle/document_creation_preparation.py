"""Read-only M11 preparation from an owned, frozen M10 input; callers hold the lock."""

import json
import sqlite3
from typing import Any

from . import answer_scope, answer_tasks, auth, local_query_tasks
from . import document_creation_contract as contract
from .config import Config, IntegrityError, InvalidInput
from .inputs import canonical
from .objects import digest


def _capture(config: Config, db: sqlite3.Connection, client_id: str, query_task_id: str) -> str:
    task = local_query_tasks.get(db, client_id, query_task_id)
    if task["state"] not in {"queued", "succeeded"}:
        raise InvalidInput("document_input_task_not_ready")
    try:
        response = local_query_tasks.current(config, db, task)
    except IntegrityError:
        raise IntegrityError("document_input_changed; prepare a new input explicitly") from None
    request = json.loads(task["request_json"])
    return canonical(
        {
            "client_id": client_id,
            "query_task_id": query_task_id,
            "binding_sha256": digest(
                canonical(
                    {
                        "request": request,
                        "snapshot": json.loads(task["snapshot_json"]),
                        "roots": json.loads(task["roots_json"]),
                    }
                ).encode()
            ),
            "vault_ids": answer_scope.stored(task, request),
            "source_version_id": task["version_id"],
            "sources": answer_tasks.snapshot(response),
            "quotations": response["quotations"],
        }
    )


def prepare(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    query_task_id: str,
    requirements: str,
    requested_format: str,
) -> contract.CreationPlan:
    """No task admission, provider access, file writes, publication or implicit source expansion."""
    plan = contract.CreationPlan(
        client_id,
        query_task_id,
        _capture(config, db, client_id, query_task_id),
        (contract.requirement(requirements),),
        requested_format,
    )
    contract.validate(plan)
    return plan


def check(
    config: Config, db: sqlite3.Connection, client_id: str, plan: contract.CreationPlan
) -> dict[str, Any]:
    contract.validate(plan)
    if plan.client_id != client_id:
        raise auth.Denied(404)
    if _capture(config, db, client_id, plan.query_task_id) != plan.input_json:
        raise IntegrityError("document_input_changed; prepare a new input explicitly")
    return {"state": "prepared", "plan_sha256": plan.sha256, **plan.value()}


def amend(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    plan: contract.CreationPlan,
    addition: str,
    *,
    expected_sha256: str,
    phase: str,
) -> contract.CreationPlan:
    check(config, db, client_id, plan)
    return contract.amend(plan, addition, expected_sha256=expected_sha256, phase=phase)


def artifact(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    plan: contract.CreationPlan,
    content: bytes,
    filename: str,
    *,
    expected_sha256: str,
) -> dict[str, Any]:
    check(config, db, client_id, plan)
    return contract.artifact(plan, content, filename, expected_sha256=expected_sha256)
