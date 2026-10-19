"""Explicit one-shot compiler launch and workpack continuation; no uncertain replay."""

import sqlite3
import subprocess
import sys
import threading
from typing import Any

from . import database, expert_tasks, source_imports
from .config import Config, InvalidInput, StorageError, contained, make_directory
from .inputs import canonical
from .query_policy_store import write_private
from .runtime_environment import child_environment


def launch(config: Config, db: sqlite3.Connection, client_id: str, identity: str) -> dict[str, Any]:
    expert_tasks.prepare(config, db, client_id, identity)
    from . import expert_authoring

    if expert_authoring.needed(config, db, client_id, identity):
        from .expert_authoring_runner import launch as launch_authoring

        return launch_authoring(config, db, client_id, identity)
    task = expert_tasks.get(db, identity)
    assert task is not None
    if task["state"] != "prepared" or task["launch_state"] != "prepared":
        return source_imports.projection(db, source_imports.get(config, db, client_id, identity))
    db.execute("UPDATE source_expert_tasks SET launch_state='launched' WHERE id=?", (identity,))
    root = contained(config.state_root, "configs/source-runs")
    make_directory(root)
    path = root / (identity + ".json")
    write_private(path, canonical({"storage": config.storage_configuration()}).encode())
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "fiber_bundle.source_import_runner",
                "--project-root",
                str(config.project_root),
                "--configuration",
                str(path),
                "--run-id",
                identity,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=child_environment(),
            start_new_session=True,
        )
    except OSError:
        db.execute(
            "UPDATE source_expert_tasks SET launch_state='unknown',state='interrupted' WHERE id=?",
            (identity,),
        )
        raise
    threading.Thread(target=_watch, args=(config, identity, process), daemon=True).start()
    return source_imports.projection(db, source_imports.get(config, db, client_id, identity))


def _watch(config: Config, identity: str, process: subprocess.Popen[bytes]) -> None:
    if process.wait() == 0:
        return
    try:
        with database.lock_wait(5), database.locked(config):
            db = database.connect(config.state_root)
            try:
                db.execute(
                    "UPDATE source_expert_tasks SET "
                    "state='interrupted',launch_state='unknown' WHERE id=? "
                    "AND state IN ('prepared','running')",
                    (identity,),
                )
                db.execute(
                    "UPDATE source_expert_attempts SET state='interrupted' WHERE task_id=? "
                    "AND state='running'",
                    (identity,),
                )
            finally:
                db.close()
    except (StorageError, TimeoutError):
        return


def resume(
    config: Config, db: sqlite3.Connection, client_id: str, identity: str, workpack_id: str | None
) -> dict[str, Any]:
    source, _, _ = expert_tasks.authorize(config, db, client_id, identity)
    task = expert_tasks.get(db, identity)
    if task is None or task["state"] not in {"paused", "completed"}:
        raise InvalidInput("expert_resume_unavailable")
    if task["state"] == "completed":
        return source_imports.projection(db, source)
    same = workpack_id == task["workpack_import_id"]
    if not same and task["workpack_import_id"] is not None:
        raise InvalidInput("expert_new_workpack_requires_new_import")
    expert_tasks.workpack(config, db, client_id, source["vault_id"], workpack_id)
    # Re-run canonical resume on a preserved copy; adding a workpack needs a fresh plan.
    db.execute(
        "UPDATE source_expert_tasks SET workpack_import_id=?,state='prepared',"
        "launch_state='prepared',next_command=? WHERE id=?",
        (workpack_id, "resume" if same else "start", identity),
    )
    return launch(config, db, client_id, identity)
