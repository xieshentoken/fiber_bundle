"""Start one finite independent worker; persisted admission precedes process IO."""

import json
import subprocess
import sys
import threading
import time

from . import database, query_jobs, query_policy_store
from .config import Config, StorageError, contained
from .runtime_environment import child_environment


def launch(config: Config, client_id: str, job_id: str) -> None:
    # Guard holds the storage lock. The child waits until the HTTP response releases it.
    db = database.connect(config.state_root)
    try:
        row = query_jobs.get(db, client_id, job_id)
        if row["state"] != "reserved":
            return
        # A committed running admission stops a second request from launching another process.
        db.execute(
            "UPDATE query_jobs SET state='running' WHERE id=? AND state='reserved'", (job_id,)
        )
        try:
            config.validate()
            path = contained(config.state_root, "query-runner-config.json")
            query_policy_store.write_private(
                path,
                json.dumps(config.storage_configuration()).encode(),
            )
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "fiber_bundle.query_job_runner",
                    "--project-root",
                    str(config.project_root),
                    "--config",
                    str(path),
                    "--job-id",
                    job_id,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=child_environment(),
                start_new_session=True,
            )
        except (OSError, ValueError, StorageError):
            db.execute(
                "UPDATE query_jobs SET state='interrupted',error_code='query_launch_failed',"
                "updated_at=? WHERE id=?",
                (time.time(), job_id),
            )
            raise
        threading.Thread(target=process.wait, daemon=True).start()
    finally:
        db.close()
