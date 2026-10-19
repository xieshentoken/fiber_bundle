"""Bound Pi extraction orchestration. Original bytes and candidate text never enter Pi."""

import json
import sqlite3
import time
from typing import Any

from . import database, document_capture, document_worker
from . import document_bindings as bindings
from . import execution_ledger as ledger
from .config import Config, IntegrityError, InvalidInput, contained
from .document_contract import MAX_SOURCE_BYTES
from .document_source import DocumentFile, binding
from .execution_config import ExecutionConfig


def active(
    config: Config,
    db: sqlite3.Connection,
    run_id: str,
    policy: ExecutionConfig | str,
    *,
    verify_file: bool = True,
) -> tuple[sqlite3.Row, bindings.DocumentSource]:
    run = ledger.get_run(db, run_id)
    expected = policy if isinstance(policy, str) else policy.digest
    if isinstance(policy, str) and run["binding_kind"] != "document":
        raise InvalidInput("deterministic document binding required")
    if run["state"] != "running" or run["config_digest"] != expected:
        raise InvalidInput("document execution not active")
    task = bindings.get(db, run_id)
    if task is None:
        raise InvalidInput("execution is not a document task")
    bound = bindings.authorize(config, db, run, task, verify_file=verify_file)
    return task, bound


def verified_files(
    config: Config, task: sqlite3.Row, source: bytes | DocumentFile
) -> dict[str, Any]:
    root = contained(config.state_root, "document-captures/" + task["capture_task_id"])
    source_sha, source_size = binding(source, MAX_SOURCE_BYTES)
    DocumentFile(contained(root, "input/source.pdf"), source_sha, source_size).verify()
    output = "output"
    result = document_capture.verify(contained(root, output), source)
    if task["profile"] == "ocr":
        from .document_ocr import verify_runtime

        runtime = json.loads(task["runtime_json"])
        verify_runtime(runtime)
        if result.get("runtime") != runtime:
            raise IntegrityError("document OCR runtime changed")
    elif task["profile"] == "conversion":
        from .document_conversion import verify_mapping
        from .document_conversion import verify_runtime as verify_conversion

        runtime = json.loads(task["runtime_json"])
        verify_conversion(runtime)
        verify_mapping(result, runtime)
    expected_format = (
        "pdf-ocr" if task["format"] == "pdf" and task["profile"] == "ocr" else task["format"]
    )
    if result["format"] != expected_format:
        raise IntegrityError("document result format changed")
    if task["state"] == "extracted" and (result["result_sha256"], *counts(result)) != (
        task["result_sha256"],
        task["page_count"],
        task["ocr_page_count"],
        task["review_page_count"],
    ):
        raise IntegrityError("document candidate changed")
    return result


def counts(result: dict[str, Any]) -> tuple[int, int, int]:
    if result["schema"] == 1:
        return (
            result["page_count"],
            len(result["pages_needing_ocr"]),
            len(result["pages_needing_review"]),
        )
    return (
        len(result["segments"]),
        sum(s["ocr_required"] for s in result["segments"]),
        sum(bool(s["review_reasons"]) for s in result["segments"]),
    )


def verify_complete(config: Config, db: sqlite3.Connection, run: sqlite3.Row) -> None:
    task = bindings.get(db, run["id"])
    if task is None or task["state"] != "extracted":
        raise InvalidInput("document extraction not complete")
    bound = bindings.authorize(config, db, run, task)
    result = verified_files(config, task, bound.file)
    from .source_processing_contract import check_result
    from .source_task_policy import frozen

    plan = frozen(db, run["id"])
    if plan is not None:
        check_result(plan, result)


def extract_bound(
    config: Config, policy: ExecutionConfig | str, run_id: str, body: object, *, deadline: float
) -> dict[str, Any]:
    if not isinstance(body, dict) or body != {}:
        raise InvalidInput("document tool accepts no caller parameters")

    def guard() -> None:
        if time.monotonic() >= deadline:
            raise InvalidInput("document execution deadline exceeded")
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                active(config, db, run_id, policy, verify_file=False)
            finally:
                db.close()

    guard()
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            with database.transaction(db):
                task, bound = active(config, db, run_id, policy)
                cached = task["state"] == "extracted"
                if not cached:
                    if task["state"] != "queued":
                        raise InvalidInput("document already attempted; explicit new task required")
                    db.execute(
                        "UPDATE document_tasks SET state='extracting' WHERE run_id=?", (run_id,)
                    )
        finally:
            db.close()
    root = contained(config.state_root, "document-captures/" + task["capture_task_id"])
    if cached:
        verified_files(config, task, bound.file)
        guard()
        return bindings.status(task)
    try:
        extra = (
            {}
            if task["format"] == "pdf" and task["profile"] == "native"
            else {
                "format": task["format"],
                "profile": task["profile"],
                "runtime": None
                if task["runtime_json"] is None
                else json.loads(task["runtime_json"]),
            }
        )
        from .source_task_policy import frozen

        with database.locked(config):
            limits_db = database.connect(config.state_root)
            try:
                plan = frozen(limits_db, run_id)
            finally:
                limits_db.close()
        if plan is not None and task["format"] == "pdf" and task["profile"] == "native":
            from .document_capacity import DEFAULT

            extra["limits"] = {key: plan["limits"][key] for key in DEFAULT}
        captured = document_worker.execute_document(
            config,
            document_worker.DocumentPolicy(task["worker_image"], task["worker_timeout"]),
            bound.file if isinstance(policy, str) else bound.data,
            guard=guard,
            task_id=task["capture_task_id"],
            **extra,
        )
        if captured.directory != root or captured.task_id != task["capture_task_id"]:
            raise InvalidInput("document worker returned different scope")
        # Host verifies independently; no trust in the worker's returned summary.
        result = verified_files(config, task, bound.file)
        from .source_processing_contract import check_result
        from .source_task_policy import frozen

        with database.locked(config):
            check_db = database.connect(config.state_root)
            try:
                plan = frozen(check_db, run_id)
                if plan is not None:
                    check_result(plan, result)
            finally:
                check_db.close()
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                with database.transaction(db):
                    current, _ = active(config, db, run_id, policy)
                    if current["state"] != "extracting":
                        raise InvalidInput("document attempt stopped")
                    db.execute(
                        "UPDATE document_tasks SET state='extracted',result_sha256=?,page_count=?,"
                        "ocr_page_count=?,review_page_count=?,error_code=NULL WHERE run_id=?",
                        (
                            result["result_sha256"],
                            *counts(result),
                            run_id,
                        ),
                    )
                    completed = bindings.get(db, run_id)
                    assert completed is not None
                    return bindings.status(completed)
            finally:
                db.close()
    except BaseException:
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                db.execute(
                    "UPDATE document_tasks SET state='failed',error_code='document_extract_failed' "
                    "WHERE run_id=? AND state='extracting'",
                    (run_id,),
                )
            finally:
                db.close()
        raise


def _interrupt(db: sqlite3.Connection, run_id: str) -> sqlite3.Row | None:
    task = bindings.get(db, run_id)
    if task is None or task["state"] not in ("queued", "extracting", "interrupted", "failed"):
        return None
    db.execute(
        "UPDATE document_tasks SET state='interrupted',error_code='document_interrupted' "
        "WHERE run_id=?",
        (run_id,),
    )
    return task


def _stop_worker(config: Config, task: sqlite3.Row | None) -> None:
    if task is None or task["state"] == "queued":
        return
    document_worker.stop_owned(
        config,
        document_worker.DocumentPolicy(task["worker_image"], task["worker_timeout"]),
        task["capture_task_id"],
    )


def stop_locked(config: Config, db: sqlite3.Connection, run_id: str) -> None:
    """HTTP caller already holds the main and exact run locks."""
    _stop_worker(config, _interrupt(db, run_id))


def stop(config: Config, run_id: str) -> None:
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            task = _interrupt(db, run_id)
        finally:
            db.close()
    _stop_worker(config, task)
