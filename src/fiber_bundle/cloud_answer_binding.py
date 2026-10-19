"""Current ownership, scope and local-result checks for cloud editing.

Only the validated proposal becomes cloud input. Source metadata, retrieval
request, full evidence and credentials never enter that envelope. Quotations
already in the proposal may leave the machine, as specified by the routing mode.
"""

import hashlib
import json
import sqlite3
from typing import Any

from . import auth, local_query_tasks
from .config import Config, IntegrityError, InvalidInput
from .query_routing_boundary import cloud_answer_request
from .query_routing_config import QueryRoutingConfig


def get(db: sqlite3.Connection, run_id: str) -> sqlite3.Row | None:
    row = db.execute("SELECT * FROM cloud_answer_tasks WHERE run_id=?", (run_id,)).fetchone()
    return row if isinstance(row, sqlite3.Row) else None


def owned(db: sqlite3.Connection, client_id: str, run_id: str) -> tuple[sqlite3.Row, sqlite3.Row]:
    run = db.execute(
        "SELECT * FROM execution_runs WHERE id=? AND client_id=?", (run_id, client_id)
    ).fetchone()
    bound = get(db, run_id)
    if not isinstance(run, sqlite3.Row) or bound is None or bound["client_id"] != client_id:
        raise auth.Denied(404)
    return run, bound


def result_digest(task: sqlite3.Row) -> str:
    return hashlib.sha256((task["result_json"] or "").encode()).hexdigest()


def current(
    config: Config, db: sqlite3.Connection, run: sqlite3.Row, binding: sqlite3.Row
) -> dict[str, Any]:
    task = local_query_tasks.get(db, run["client_id"], binding["local_task_id"])
    if (
        binding["client_id"] != run["client_id"]
        or task["config_digest"] != binding["routing_digest"]
        or result_digest(task) != binding["local_result_sha256"]
        or any(task[k] != run[k] for k in ("vault_id", "version_id", "binding_kind"))
    ):
        raise IntegrityError("cloud_local_binding_changed")
    return local_query_tasks.deliver(config, db, run["client_id"], task["id"])


def envelope(response: dict[str, Any]) -> dict[str, str]:
    proposal = {"mode": response["mode"], "quotations": response["quotations"]}
    if response["mode"] == "cited_draft":
        proposal["claims"] = [
            {"text": c["text"], "citations": c["citations"]} for c in response["claims"]
        ]
    return {
        "schema": "local-answer/v1",
        "answer": json.dumps(proposal, ensure_ascii=False, sort_keys=True),
    }


def prepare(
    route: QueryRoutingConfig, answer: dict[str, str], *, backup_index: int | None = None
) -> dict[str, Any]:
    payload = cloud_answer_request(route, answer, backup_index=backup_index)
    # The existing provider sender uses escaped JSON; check its actual wire size.
    if len(json.dumps(payload).encode()) > route.answer_policy(backup_index).max_request_bytes:
        raise InvalidInput("cloud_request_too_large")
    return payload
