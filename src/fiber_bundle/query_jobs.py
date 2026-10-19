"""Durable admission, owned status and cancellation; no model IO here."""

import sqlite3
import time
from typing import Any
from uuid import uuid4

from . import auth, cloud_answer_tasks, database, local_query_ledger, local_query_tasks
from .config import Config, InvalidInput
from .query_routing_config import QueryRoutingConfig


def get(db: sqlite3.Connection, client_id: str, job_id: str) -> sqlite3.Row:
    row = db.execute(
        "SELECT * FROM query_jobs WHERE id=? AND client_id=?", (job_id, client_id)
    ).fetchone()
    if not isinstance(row, sqlite3.Row):
        raise auth.Denied(404)
    active = db.execute("SELECT active FROM clients WHERE id=?", (client_id,)).fetchone()
    if active is None or not active[0]:
        raise auth.Denied(401)
    return row


def view(db: sqlite3.Connection, client_id: str, job_id: str) -> dict[str, Any]:
    row = get(db, client_id, job_id)
    return dict(row)


def submit(
    config: Config,
    db: sqlite3.Connection,
    route: QueryRoutingConfig,
    client_id: str,
    role: str,
    task_id: str,
    cloud_edit: bool = False,
) -> dict[str, Any]:
    route.validate()
    from .query_policy_store import freeze

    freeze(config, route)
    if (
        role not in ("local", "cloud")
        or type(cloud_edit) is not bool
        or (role == "cloud" and cloud_edit)
    ):
        raise InvalidInput("query_job_invalid")
    if cloud_edit:
        route.answer_policy()
    if role == "local":
        task = local_query_tasks.get(db, client_id, task_id)
        local_query_tasks.current(config, db, task)
        if task["config_digest"] != route.digest:
            raise InvalidInput("routing_revision_conflict")
        calls = db.execute("SELECT 1 FROM local_query_calls WHERE task_id=?", (task_id,)).fetchone()
    else:
        task, _ = cloud_answer_tasks.get(db, client_id, task_id)
        cloud_answer_tasks.current(config, db, route, client_id, task_id)
        calls = db.execute("SELECT 1 FROM model_calls WHERE run_id=?", (task_id,)).fetchone()
    prior = db.execute(
        "SELECT id,client_id,cloud_edit,routing_digest FROM query_jobs WHERE role=? AND task_id=?",
        (role, task_id),
    ).fetchone()
    if prior is not None:
        if (
            prior["client_id"] != client_id
            or prior["cloud_edit"] != cloud_edit
            or prior["routing_digest"] != route.digest
        ):
            raise InvalidInput("query_job_conflict")
        return view(db, client_id, prior["id"])
    if (
        task["state"] != "queued"
        or calls is not None
        or (role == "cloud" and task["attempts"] != 0)
    ):
        raise InvalidInput("query_attempt_not_replayable")
    job_id = str(uuid4())
    now = time.time()
    with database.transaction(db):
        db.execute(
            "INSERT INTO query_jobs VALUES(?,?,?,?,?,?,'reserved',NULL,NULL,?,?)",
            (job_id, client_id, role, task_id, route.digest, int(cloud_edit), now, now),
        )
    return view(db, client_id, job_id)


def cancel(config: Config, db: sqlite3.Connection, client_id: str, job_id: str) -> dict[str, Any]:
    row = get(db, client_id, job_id)
    if row["state"] not in ("reserved", "running"):
        return view(db, client_id, job_id)
    if row["role"] == "local":
        local_query_ledger.stop(config, db, client_id, row["task_id"])
    else:
        cloud_answer_tasks.stop(db, client_id, row["task_id"])
    linked = db.execute(
        "SELECT run_id FROM cloud_answer_tasks WHERE client_id=? AND submission_id=?",
        (client_id, "query-job:" + job_id),
    ).fetchone()
    if linked is not None:
        cloud_answer_tasks.stop(db, client_id, linked[0])
    db.execute(
        "UPDATE query_jobs SET state='cancelled',updated_at=? WHERE id=?", (time.time(), job_id)
    )
    return view(db, client_id, job_id)
