"""Durable, single-attempt offline media lifecycle; no model or publication ledger effects."""

import hashlib
import re
import sqlite3
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

from . import database, execution_scheduler
from . import media_bindings as bindings
from . import media_worker as worker
from .auth import Denied
from .config import Config, IntegrityError, InvalidInput
from .media_candidate import request
from .media_capture import verify, verify_snapshot
from .media_files import MAX_RESULT_BYTES, canonical, decode, read
from .media_worker import MediaPolicy


@contextmanager
def connection(config: Config) -> Iterator[sqlite3.Connection]:
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            yield db
        finally:
            db.close()


def create(
    config: Config,
    client_id: str,
    receipt_id: str,
    asset_id: str,
    mode: str,
    declared_duration_ms: int,
    policy: MediaPolicy,
    *,
    subtitle_receipt_id: str | None = None,
    subtitle_asset_id: str | None = None,
) -> str:
    with connection(config) as db:
        return create_locked(
            config,
            db,
            client_id,
            receipt_id,
            asset_id,
            mode,
            declared_duration_ms,
            policy,
            subtitle_receipt_id=subtitle_receipt_id,
            subtitle_asset_id=subtitle_asset_id,
        )


def create_locked(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    receipt_id: str,
    asset_id: str,
    mode: str,
    declared_duration_ms: int,
    policy: MediaPolicy,
    *,
    subtitle_receipt_id: str | None = None,
    subtitle_asset_id: str | None = None,
) -> str:
    policy.validate()
    if (subtitle_receipt_id is None) != (subtitle_asset_id is None):
        raise InvalidInput("media_subtitle_pair")
    original, _ = bindings.source(config, db, client_id, receipt_id, asset_id)
    subtitle = None
    if subtitle_receipt_id is not None and subtitle_asset_id is not None:
        subtitle, _ = bindings.source(
            config,
            db,
            client_id,
            subtitle_receipt_id,
            subtitle_asset_id,
            subtitle=True,
        )
        if subtitle["vault_id"] != original["vault_id"]:
            raise Denied(404)
    spec = {
        "format": original["format"],
        "evidence_mode": mode,
        "declared_duration_ms": declared_duration_ms,
        "subtitle_kind": subtitle["format"] if subtitle else None,
    }
    request(spec)
    bound = {
        "source": original,
        "subtitle": subtitle,
        "request": spec,
        "image": policy.image,
        "timeout_seconds": policy.timeout_seconds,
    }
    identifier = str(uuid4())
    now = time.time()
    with database.transaction(db):
        db.execute(
            "INSERT INTO media_tasks(id,client_id,vault_id,binding_json,state,created_at,"
            "updated_at) VALUES(?,?,?,?,'queued',?,?)",
            (identifier, client_id, original["vault_id"], canonical(bound).decode(), now, now),
        )
    return identifier


def load(db: sqlite3.Connection, client_id: str, job_id: str) -> sqlite3.Row:
    row = db.execute(
        "SELECT * FROM media_tasks WHERE id=? AND client_id=?",
        (job_id, client_id),
    ).fetchone()
    if not isinstance(row, sqlite3.Row):
        raise Denied(404)
    return row


def binding(row: sqlite3.Row) -> dict[str, Any]:
    value = decode(row["binding_json"].encode())
    if set(value) != {"source", "subtitle", "request", "image", "timeout_seconds"}:
        raise IntegrityError("media_task_binding")
    request(value["request"])
    if value["request"]["format"] != value["source"]["format"] or (
        value["request"]["subtitle_kind"]
        != (value["subtitle"]["format"] if value["subtitle"] else None)
    ):
        raise IntegrityError("media_request_binding")
    MediaPolicy(value["image"], value["timeout_seconds"]).validate()
    if value["source"]["vault_id"] != row["vault_id"]:
        raise IntegrityError("media_task_vault")
    return value


def summary(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "job_id": row["id"],
        "state": row["state"],
        "error_code": row["error_code"],
        "publication_ready": False,
        "knowledge_verified": False,
        "cleanup_allowed": False,
        "model_calls": 0,
    }


def status(config: Config, client_id: str, job_id: str) -> dict[str, Any]:
    with connection(config) as db:
        row = load(db, client_id, job_id)
        bindings.authorize(config, db, client_id, binding(row))
        return summary(row)


def result(config: Config, client_id: str, job_id: str) -> dict[str, Any]:
    with connection(config) as db:
        row = load(db, client_id, job_id)
        bound = binding(row)
        source, subtitle = bindings.authorize(config, db, client_id, bound, permission="query")
        if row["state"] != "complete":
            raise InvalidInput("media_candidate_unavailable")
        root = worker.directory(config, job_id)
        verify_snapshot(root / "input", source, subtitle, bound["request"])
        outputs = root / "output"
        if (
            hashlib.sha256(read(outputs, "result.json", MAX_RESULT_BYTES)).hexdigest()
            != (row["result_sha256"])
        ):
            raise IntegrityError("media_result_changed")
        return verify(outputs, source, subtitle, bound["request"])


def run(config: Config, client_id: str, job_id: str) -> dict[str, Any]:
    with execution_scheduler.admit(config, "heavy"):
        return _run(config, client_id, job_id)


def _run(config: Config, client_id: str, job_id: str) -> dict[str, Any]:
    with connection(config) as db:
        row = load(db, client_id, job_id)
        bound = binding(row)
        source, subtitle = bindings.authorize(config, db, client_id, bound)
        if row["state"] != "queued":
            raise InvalidInput("media_task_no_replay")
        with database.transaction(db):
            db.execute(
                "UPDATE media_tasks SET state='running',updated_at=? WHERE id=?",
                (time.time(), job_id),
            )

    def guard() -> None:
        with connection(config) as current:
            active = load(current, client_id, job_id)
            if active["state"] != "running" or binding(active) != bound:
                raise InvalidInput("media_task_stopped_or_changed")
            bindings.authorize(config, current, client_id, bound)

    try:
        worker.execute(
            config,
            MediaPolicy(bound["image"], bound["timeout_seconds"]),
            job_id,
            source,
            subtitle,
            bound["request"],
            guard=guard,
        )
        with connection(config) as db:
            active = load(db, client_id, job_id)
            if active["state"] != "running" or binding(active) != bound:
                raise InvalidInput("media_task_stopped_or_changed")
            current_source, current_subtitle = bindings.authorize(config, db, client_id, bound)
            root = worker.directory(config, job_id)
            verify_snapshot(root / "input", current_source, current_subtitle, bound["request"])
            outputs = root / "output"
            verify(outputs, current_source, current_subtitle, bound["request"])
            sha = hashlib.sha256(read(outputs, "result.json", MAX_RESULT_BYTES)).hexdigest()
            with database.transaction(db):
                db.execute(
                    "UPDATE media_tasks SET state='complete',result_sha256=?,updated_at=? "
                    "WHERE id=?",
                    (sha, time.time(), job_id),
                )
            return summary(load(db, client_id, job_id))
    except BaseException as exc:
        error = "media_attempt_failed"
        if isinstance(exc, Denied):
            error = "media_permission_denied"
        elif isinstance(exc, subprocess.TimeoutExpired):
            error = "media_worker_timeout"
        elif isinstance(exc, (InvalidInput, IntegrityError)) and re.fullmatch(
            r"media_[a-z_]{1,50}", str(exc)
        ):
            error = str(exc)
        try:
            with connection(config) as db, database.transaction(db):
                db.execute(
                    "UPDATE media_tasks SET state='failed',error_code=?,updated_at=? "
                    "WHERE id=? AND state='running'",
                    (error, time.time(), job_id),
                )
        except (OSError, sqlite3.Error, InvalidInput, IntegrityError) as persistence_error:
            exc.add_note("media failure status could not be persisted")
            raise exc from persistence_error
        raise


def stop(config: Config, client_id: str, job_id: str, *, recover: bool = False) -> dict[str, Any]:
    with connection(config) as db:
        row = load(db, client_id, job_id)
        bound = binding(row)
        bindings.authorize(config, db, client_id, bound)
        allowed = {"queued", "running", "cancelled", "interrupted"} | (
            {"failed"} if recover else set()
        )
        if row["state"] not in allowed:
            raise InvalidInput("media_task_terminal")
        with database.transaction(db):
            db.execute(
                "UPDATE media_tasks SET state=?,updated_at=? WHERE id=?",
                ("interrupted" if recover else "cancelled", time.time(), job_id),
            )
    # A failed stop remains terminal and is explicit operator recovery, never re-execution.
    if row["state"] == "running" or worker.directory(config, job_id).exists():
        worker.stop_owned(config, MediaPolicy(bound["image"], bound["timeout_seconds"]), job_id)
    return status(config, client_id, job_id)
