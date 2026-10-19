"""Launch only one explicitly confirmed child; preserve interrupted admissions."""

import sqlite3
import subprocess
import sys
import threading
import time
from typing import Any

from . import database
from .config import Config, contained, make_directory
from .inputs import canonical
from .query_policy_store import write_private
from .runtime_environment import child_environment
from .vault_jobs import row, view


def launch(config: Config, db: sqlite3.Connection, jid: str) -> dict[str, Any]:
    job = row(db, jid)
    if job["state"] != "prepared":
        return view(job)
    make_directory(contained(config.state_root, "vault-jobs"))
    path = contained(config.state_root, "vault-jobs/" + jid + ".json")
    write_private(path, canonical(config.storage_configuration()).encode())
    db.execute(
        "UPDATE vault_operations SET state='launch_requested',updated_at=? WHERE id=?",
        (time.time(), jid),
    )
    try:
        child = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "fiber_bundle.vault_job_cli",
                "--project-root",
                str(config.project_root),
                "--config",
                str(path),
                "--job-id",
                jid,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=child_environment(),
            start_new_session=True,
        )
    except OSError:
        db.execute("UPDATE vault_operations SET state='failed' WHERE id=?", (jid,))
        raise

    def observe() -> None:
        child.wait()
        with database.locked(config):
            main = database.connect(config.state_root)
            try:
                main.execute(
                    "UPDATE vault_operations SET state='interrupted',updated_at=? "
                    "WHERE id=? AND state IN ('launch_requested','running')",
                    (time.time(), jid),
                )
            finally:
                main.close()

    threading.Thread(target=observe, daemon=True, name="vault-job-observer").start()
    return view(row(db, jid))
