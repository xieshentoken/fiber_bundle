"""Admission and finite execution of model-free imported documents."""

import json
import logging
import sqlite3
import subprocess
import sys
import threading
import time
from typing import Any
from uuid import uuid4

from . import (
    database,
    document_bindings,
    document_tasks,
    objects,
    source_imports,
    source_task_policy,
)
from . import execution_ledger as ledger
from .config import Config, InvalidInput, StorageError, contained, make_directory
from .document_worker import DocumentPolicy
from .executor_container import run_lock
from .inputs import canonical
from .query_policy_store import write_private
from .runtime_environment import child_environment
from .source_processing_contract import worker_binding
from .source_task_guard import SourceBinding, bind


def prepare(config: Config, db: sqlite3.Connection, client_id: str, import_id: str) -> str:
    row = source_imports.get(config, db, client_id, import_id)
    plan = json.loads(row["plan_json"])
    if plan["engine"] == "fake-expert":
        from .expert_tasks import prepare as prepare_expert

        return prepare_expert(config, db, client_id, import_id)
    if row["state"] != "archived" or plan["action"] != "process" or plan["engine"] != "ordinary":
        raise InvalidInput("import not prepared for deterministic processing")
    if plan["format"] == "archive":
        from .chat_archive import require

        require(config, db, row)
    if row["run_id"]:
        ledger.authorize(config, db, ledger.get_run(db, row["run_id"]))
        return str(row["run_id"])
    receipt = db.execute(
        "SELECT source_version_id FROM receipts WHERE job_id=?", (row["receipt_id"],)
    ).fetchone()
    request = dict(
        client_id=client_id,
        vault_id=row["vault_id"],
        receipt_id=row["receipt_id"],
        asset_id=row["asset_id"],
        source_sha256=plan["source_sha256"],
        kind="document",
        submission_id=row["submission_id"],
        configuration_sha256=objects.digest(row["request_json"].encode()),
        model_configuration_sha256=None,
    )
    run_id = str(uuid4())
    image, runtime = worker_binding(plan)
    with database.transaction(db):
        db.execute(
            "INSERT INTO execution_runs(id,client_id,vault_id,version_id,confi"
            "g_digest,state,attempts,task_limit_nusd,currency,created_at,updat"
            "ed_at,binding_kind) VALUES(?,?,?,?,?,'queued',0,0,'USD',?,?,'docu"
            "ment')",
            (
                run_id,
                client_id,
                row["vault_id"],
                receipt[0],
                plan["plan_sha256"],
                time.time(),
                time.time(),
            ),
        )
        run = ledger.get_run(db, run_id)
        document_bindings.bind(
            config,
            db,
            run,
            document_bindings.DocumentInput(
                row["receipt_id"],
                row["asset_id"],
                DocumentPolicy(image, plan["policy"]["timeout_seconds"]),
                profile=plan["profile"],
                runtime=runtime,
            ),
        )
        bind(
            db,
            run,
            SourceBinding(
                row["receipt_id"],
                receipt[0],
                row["asset_id"],
                plan["source_sha256"],
                None,
                None,
                row["submission_id"],
                source_task_policy.envelope(request, plan),
            ),
        )
        db.execute("UPDATE source_imports SET run_id=? WHERE id=?", (run_id, import_id))
        ledger.authorize(config, db, run)
    return run_id


def launch(
    config: Config, db: sqlite3.Connection, client_id: str, import_id: str
) -> dict[str, Any]:
    if (
        json.loads(source_imports.get(config, db, client_id, import_id)["plan_json"])["engine"]
        == "fake-expert"
    ):
        from .expert_dispatch import launch as launch_expert

        return launch_expert(config, db, client_id, import_id)
    run_id = prepare(config, db, client_id, import_id)
    run = ledger.get_run(db, run_id)
    bound = db.execute(
        "SELECT launch_state FROM source_task_bindings WHERE run_id=?", (run_id,)
    ).fetchone()
    if bound[0] != "prepared" or run["state"] != "queued":
        return source_imports.projection(db, source_imports.get(config, db, client_id, import_id))
    db.execute("UPDATE source_task_bindings SET launch_state='launched' WHERE run_id=?", (run_id,))
    try:
        root = contained(config.state_root, "configs/source-runs")
        make_directory(root)
        path = root / (run_id + ".json")
        write_private(path, canonical({"storage": config.storage_configuration()}).encode())
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
                run_id,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=child_environment(),
            start_new_session=True,
        )
    except (OSError, InvalidInput):
        db.execute(
            "UPDATE source_task_bindings SET launch_state='unknown' WHERE run_id=?", (run_id,)
        )
        ledger.transition(db, run_id, "interrupted", "import_launch_uncertain")
        raise
    threading.Thread(target=_watch, args=(config, run_id, process), daemon=True).start()
    return source_imports.projection(db, source_imports.get(config, db, client_id, import_id))


def _watch(config: Config, run_id: str, process: subprocess.Popen[bytes]) -> None:
    code = process.wait()
    if code == 0:
        return
    try:
        with database.lock_wait(5), run_lock(config, run_id):
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    if ledger.get_run(db, run_id)["state"] not in {"queued", "running"}:
                        return
                finally:
                    db.close()
            document_tasks.stop(config, run_id)
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    if ledger.get_run(db, run_id)["state"] in {"queued", "running"}:
                        ledger.transition(db, run_id, "interrupted", "import_runner_exited")
                        db.execute(
                            "UPDATE source_task_bindings SET launch_state='unknown' WHERE run_id=?",
                            (run_id,),
                        )
                finally:
                    db.close()
    except (StorageError, TimeoutError):
        # Preserve the consumed launch intent; never retry an uncertain process.
        logging.getLogger(__name__).warning("import runner reconciliation unavailable: %s", run_id)


def execute(config: Config, run_id: str) -> None:
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            is_expert = (
                db.execute("SELECT 1 FROM source_expert_tasks WHERE id=?", (run_id,)).fetchone()
                is not None
            )
        finally:
            db.close()
    if is_expert:
        from .expert_execution import execute as execute_expert

        execute_expert(config, run_id)
        return
    with run_lock(config, run_id):
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                with database.transaction(db):
                    run = ledger.get_run(db, run_id)
                    ledger.authorize(config, db, run)
                    plan = source_task_policy.frozen(db, run_id)
                    if (
                        plan is None
                        or run["binding_kind"] != "document"
                        or run["state"] != "queued"
                        or run["attempts"] != 0
                        or run["task_limit_nusd"] != 0
                        or run["config_digest"] != plan["plan_sha256"]
                    ):
                        raise InvalidInput("deterministic import cannot start")
                    db.execute(
                        "UPDATE execution_runs SET state='running',attempts=1,updated_at=?"
                        " WHERE id=?",
                        (time.time(), run_id),
                    )
            finally:
                db.close()
        try:
            document_tasks.extract_bound(
                config,
                plan["plan_sha256"],
                run_id,
                {},
                deadline=time.monotonic() + plan["policy"]["timeout_seconds"],
            )
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    with database.transaction(db):
                        run = ledger.get_run(db, run_id)
                        ledger.authorize(config, db, run)
                        if run["state"] != "running":
                            raise InvalidInput("import stopped before completion")
                        document_tasks.verify_complete(config, db, run)
                        if plan["format"] == "markdown":
                            from .source_import_markdown import references
                            from .source_import_references import bound as mapped

                            task = document_bindings.get(db, run_id)
                            assert task is not None
                            source = document_bindings.authorize(config, db, run, task)
                            candidate = document_tasks.verified_files(config, task, source.file)
                            missing = set(
                                references("\n".join(u["text"] for u in candidate["segments"]))
                            ) - set(mapped(config, db, run_id))
                            if missing:
                                db.execute(
                                    "UPDATE document_tasks SET "
                                    "error_code='markdown_references_missing' "
                                    "WHERE run_id=?",
                                    (run_id,),
                                )
                        ledger.transition(db, run_id, "succeeded")
                finally:
                    db.close()
        except BaseException:
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    ledger.transition(db, run_id, "failed", "import_extract_failed")
                finally:
                    db.close()
            raise
