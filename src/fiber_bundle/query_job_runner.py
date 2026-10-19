"""One explicitly admitted local or cloud job; no polling, retries or fallback."""

import argparse
import signal
import time
from pathlib import Path
from typing import Any

from . import (
    cloud_answer_execution,
    cloud_answer_tasks,
    database,
    local_query_execution,
    local_query_ledger,
    query_jobs,
    query_policy_store,
)
from .answer_worker import worker_lock
from .config import Config, load_config
from .executor_container import run_lock


def _execute(config: Config, job_id: str) -> dict[str, Any]:
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            raw = db.execute("SELECT * FROM query_jobs WHERE id=?", (job_id,)).fetchone()
            if raw is None or raw["state"] != "running":
                return {"state": "not_run"}
            row = query_jobs.get(db, raw["client_id"], job_id)
            route = query_policy_store.version(config, row["routing_digest"])
            role, task_id, cid, edit = (
                row["role"],
                row["task_id"],
                row["client_id"],
                row["cloud_edit"],
            )
        finally:
            db.close()
    if role == "local":
        result = local_query_execution.execute(config, route, cid, task_id)
    else:
        result = cloud_answer_execution.execute(config, route, cid, task_id, allow_inference=True)
    if role == "local" and edit and result["state"] == "succeeded":
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                if query_jobs.get(db, cid, job_id)["state"] != "running":
                    return {"state": "cancelled"}
                cloud = cloud_answer_tasks.submit(
                    config, db, route, cid, task_id, "query-job:" + job_id
                )["run_id"]
                db.execute("UPDATE query_jobs SET cloud_run_id=? WHERE id=?", (cloud, job_id))
            finally:
                db.close()
        result = cloud_answer_execution.execute(config, route, cid, cloud, allow_inference=True)
    return result


def run(config: Config, job_id: str) -> dict[str, Any]:
    # Own the admission mutex even while recording a rejected/failed dispatch.
    with run_lock(config, job_id):
        reason: str | None
        state, reason = "failed", "query_job_failed"
        try:
            with worker_lock(config):
                result = _execute(config, job_id)
            state = (
                result["state"]
                if result["state"] in ("succeeded", "cancelled", "not_run")
                else "failed"
            )
            reason = result.get("error_code")
        except (KeyboardInterrupt, SystemExit):
            state, reason = "interrupted", "query_job_interrupted"
            raise
        except Exception:
            state, reason = "failed", "query_job_failed"
        finally:
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    row = db.execute("SELECT * FROM query_jobs WHERE id=?", (job_id,)).fetchone()
                    if row is not None and row["state"] == "running":
                        if state == "interrupted":
                            if row["role"] == "local":
                                local_query_ledger.stop(
                                    config, db, row["client_id"], row["task_id"], interrupt=True
                                )
                            else:
                                cloud_answer_tasks.stop(
                                    db, row["client_id"], row["task_id"], interrupt=True
                                )
                            linked = db.execute(
                                "SELECT run_id FROM cloud_answer_tasks WHERE client_id=? "
                                "AND submission_id=?",
                                (row["client_id"], "query-job:" + job_id),
                            ).fetchone()
                            if linked is not None:
                                cloud_answer_tasks.stop(
                                    db, row["client_id"], linked[0], interrupt=True
                                )
                        db.execute(
                            "UPDATE query_jobs SET state=?,error_code=?,updated_at=? WHERE id=?",
                            (state, reason, time.time(), job_id),
                        )
                finally:
                    db.close()
        return {"state": state, "error_code": reason}


def _interrupt(signum: int, frame: Any) -> None:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise KeyboardInterrupt()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project-root", type=Path, required=True)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--job-id", required=True)
    args = p.parse_args()
    signal.signal(signal.SIGTERM, _interrupt)
    try:
        return int(
            run(load_config(args.config, args.project_root), args.job_id)["state"] != "succeeded"
        )
    except (Exception, KeyboardInterrupt):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
