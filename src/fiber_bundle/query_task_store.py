"""Query task/job read projections; callers hold the lock and authorize access."""

import sqlite3
from typing import Any

from .auth import Denied
from .config import InvalidInput


def listing(db: sqlite3.Connection) -> dict[str, Any]:
    rows = db.execute(
        "SELECT t.id AS task_id,t.client_id,c.name AS client_name,'local' AS "
        "role,t.state,t.error_code,t.config_digest AS configuration_sha256,t.created_at "
        "FROM local_query_tasks t JOIN clients c ON c.id=t.client_id UNION ALL SELECT "
        "t.run_id,t.client_id,c.name,'cloud',r.state,r.error_code,"
        "t.routing_digest,r.created_at "
        "FROM cloud_answer_tasks t JOIN clients c ON c.id=t.client_id JOIN "
        "execution_runs r ON r.id=t.run_id ORDER BY 8 DESC LIMIT 100"
    ).fetchall()
    return {
        "tasks": [dict(r) for r in rows],
        "jobs": [
            dict(r)
            for r in db.execute("SELECT * FROM query_jobs ORDER BY created_at DESC LIMIT 100")
        ],
    }


def task_owner(db: sqlite3.Connection, role: str, task_id: str) -> str:
    if role == "local":
        sql = "SELECT client_id FROM local_query_tasks WHERE id=?"
    elif role == "cloud":
        sql = "SELECT client_id FROM cloud_answer_tasks WHERE run_id=?"
    else:
        raise InvalidInput("query_role_invalid")
    row = db.execute(sql, (task_id,)).fetchone()
    if row is None:
        raise Denied(404)
    return str(row[0])


def job_owner(db: sqlite3.Connection, job_id: str) -> str:
    row = db.execute("SELECT client_id FROM query_jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise Denied(404)
    return str(row[0])


def task_job(
    db: sqlite3.Connection,
    client_id: str,
    role: str,
    task_id: str,
    *,
    include_linked_cloud: bool = False,
) -> str | None:
    if include_linked_cloud:
        row = db.execute(
            "SELECT id FROM query_jobs WHERE client_id=? AND ((role=? AND task_id=?) "
            "OR cloud_run_id=?)",
            (client_id, role, task_id, task_id),
        ).fetchone()
    else:
        row = db.execute(
            "SELECT id FROM query_jobs WHERE client_id=? AND role=? AND task_id=?",
            (client_id, role, task_id),
        ).fetchone()
    return None if row is None else str(row[0])
