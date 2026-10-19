"""Explicit recovery of abandoned admissions, never a new inference attempt."""

import sqlite3
import time
from typing import Any

from . import cloud_answer_tasks, local_query_execution, local_query_ledger, query_jobs
from .config import Config, InvalidInput
from .executor_container import run_lock


def recover(config: Config, db: sqlite3.Connection, client_id: str, job_id: str) -> dict[str, Any]:
    row = query_jobs.get(db, client_id, job_id)
    if row["state"] not in ("reserved", "running"):
        return query_jobs.view(db, client_id, job_id)
    with run_lock(config, job_id), run_lock(config, row["task_id"]):
        if row["role"] == "local":
            if not local_query_execution.stop_container(row["task_id"]):
                raise InvalidInput("local_container_cleanup_unverified")
            local_query_ledger.stop(config, db, client_id, row["task_id"], interrupt=True)
        else:
            cloud_answer_tasks.stop(db, client_id, row["task_id"], interrupt=True)
        linked = db.execute(
            "SELECT run_id FROM cloud_answer_tasks WHERE client_id=? AND submission_id=?",
            (client_id, "query-job:" + job_id),
        ).fetchone()
        if linked is not None:
            with run_lock(config, linked[0]):
                cloud_answer_tasks.stop(db, client_id, linked[0], interrupt=True)
        db.execute(
            "UPDATE query_jobs SET state='interrupted',error_code='query_job_recovered',"
            "updated_at=? WHERE id=?",
            (time.time(), job_id),
        )
    return query_jobs.view(db, client_id, job_id)
