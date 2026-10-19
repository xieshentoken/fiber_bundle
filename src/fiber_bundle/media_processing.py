"""Single-attempt ASR/frame enrichment, isolated from retained M9.1 subtitle results."""

import hashlib
import json
import sqlite3
import time
from typing import Any

from . import (
    database,
    execution_scheduler,
    media_asr_runtime,
    media_bindings,
    media_glossary,
    media_jobs,
)
from . import (
    media_processing_worker as worker,
)
from .config import Config, IntegrityError, InvalidInput
from .media_capture import verify, verify_snapshot
from .media_files import MAX_RESULT_BYTES, canonical, decode, read
from .media_processing_capture import verify as verify_processing
from .media_processing_contract import plan, policy
from .media_processing_files import read as read_processing
from .media_worker import directory as parent_directory


def parent(
    config: Config, db: sqlite3.Connection, client_id: str, job_id: str, permission: str = "add"
) -> tuple[sqlite3.Row, dict[str, Any], bytes, bytes | None]:
    job = media_jobs.load(db, client_id, job_id)
    bound = media_jobs.binding(job)
    source, subtitle = media_bindings.authorize(config, db, client_id, bound, permission=permission)
    if job["state"] != "complete":
        raise InvalidInput("media_processing_parent_incomplete")
    root = parent_directory(config, job_id)
    verify_snapshot(root / "input", source, subtitle, bound["request"])
    data = read(root / "output", "result.json", MAX_RESULT_BYTES)
    if hashlib.sha256(data).hexdigest() != job["result_sha256"]:
        raise IntegrityError("media_parent_result_changed")
    value = verify(root / "output", source, subtitle, bound["request"])
    if value["probe"]["duration_ms"] != bound["request"]["declared_duration_ms"]:
        raise InvalidInput("media_processing_source_duration_mismatch")
    return job, value, source, subtitle


def get(db: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM media_processing WHERE job_id=?", (job_id,)).fetchone()
    if not isinstance(row, sqlite3.Row):
        raise InvalidInput("media_processing_missing")
    return row


def current(
    config: Config, db: sqlite3.Connection, cid: str, job_id: str, permission: str = "add"
) -> tuple[sqlite3.Row, dict[str, Any], bytes, bytes | None]:
    job, value, source, subtitle = parent(config, db, cid, job_id, permission)
    row = get(db, job_id)
    spec = plan(decode(row["plan_json"].encode()))
    if spec["asr"]:
        media_asr_runtime.require(spec["policy"])
    gate = media_glossary.gate(db, job)
    if not gate["terminology_gate_passed"] or (
        spec["source_sha256"] != value["source_sha256"]
        or spec["source_bytes"] != len(source)
        or spec["parent_result_sha256"] != job["result_sha256"]
        or spec["request"] != value["request"]
        or spec["duration_ms"] != value["probe"]["duration_ms"]
        or (spec["glossary_revision"], spec["glossary_sha256"])
        != (gate["revision"], gate["snapshot_sha256"])
    ):
        raise IntegrityError("media_processing_binding_changed")
    return row, spec, source, subtitle


def create(config: Config, cid: str, job_id: str, resources: dict[str, Any]) -> dict[str, Any]:
    p = policy(resources)
    with media_jobs.connection(config) as db:
        job, value, source, subtitle = parent(config, db, cid, job_id)
        if db.execute("SELECT 1 FROM media_processing WHERE job_id=?", (job_id,)).fetchone():
            raise InvalidInput("media_processing_no_replay")
        gate = media_glossary.gate(db, job)
        if not gate["terminology_gate_passed"]:
            raise InvalidInput("media_processing_confirmation_required")
        frozen = media_glossary.latest(db, job_id)
        if frozen is None:
            raise InvalidInput("media_processing_confirmation_required")
        if subtitle is None:
            media_asr_runtime.require(p)
        spec = {
            "schema": "fiber-media-processing-plan/v1",
            "parent_result_sha256": job["result_sha256"],
            "source_sha256": value["source_sha256"],
            "source_bytes": len(source),
            "request": value["request"],
            "duration_ms": value["probe"]["duration_ms"],
            "glossary_revision": gate["revision"],
            "glossary_sha256": gate["snapshot_sha256"],
            "glossary": media_glossary.verified(frozen),
            "policy": p,
            "asr": subtitle is None,
        }
        plan(spec)
        # UTC-day reservations include queued, failed and unknown attempts.
        now = time.time()
        day = int(now // 86400) * 86400
        if (
            db.execute(
                "SELECT count(*) FROM media_processing WHERE created_at>=?", (day,)
            ).fetchone()[0]
            >= p["daily_maximum"]
        ):
            raise InvalidInput("media_processing_daily_budget")
        with database.transaction(db):
            db.execute(
                "INSERT INTO media_processing VALUES(?,?,'queued',NULL,NULL,?,?)",
                (job_id, canonical(spec).decode(), now, now),
            )
        from .automatic_jobs import enqueue

        enqueue(config, db, cid, job["vault_id"], "media", job_id)
        return {
            "job_id": job_id,
            "state": "queued",
            "asr": spec["asr"],
            "resource_reservation": True,
            "publication_ready": False,
            "cleanup_allowed": False,
        }


def result_locked(
    config: Config, db: sqlite3.Connection, cid: str, job_id: str, permission: str = "query"
) -> dict[str, Any]:
    from .media_cleanup import archived_result

    if permission == "query":
        archived = archived_result(config, db, cid, job_id)
        if archived is not None:
            return archived
    row, spec, source, subtitle = current(config, db, cid, job_id, permission)
    if row["state"] != "complete":
        raise InvalidInput("media_processing_unavailable")
    root = worker.directory(config, job_id)
    raw = read_processing(root / "output", "result.json")
    if hashlib.sha256(raw).hexdigest() != row["result_sha256"]:
        raise IntegrityError("media_processing_result_changed")
    value = verify_processing(root / "input", root / "output", source, subtitle, spec)
    return {
        **value,
        "result_sha256": row["result_sha256"],
        "glossary_revision": spec["glossary_revision"],
        "glossary_sha256": spec["glossary_sha256"],
        "job_id": job_id,
    }


def result(config: Config, cid: str, job_id: str) -> dict[str, Any]:
    with media_jobs.connection(config) as db:
        return result_locked(config, db, cid, job_id)


def status(config: Config, cid: str, job_id: str) -> dict[str, Any]:
    with media_jobs.connection(config) as db:
        job = media_jobs.load(db, cid, job_id)
        from . import auth, vaults

        vaults.checked_vault(config, auth.grant(db, cid, job["vault_id"], "add"))
        row = get(db, job_id)
        return {
            "job_id": job_id,
            "state": row["state"],
            "error_code": row["error_code"],
            "publication_ready": False,
            "cleanup_allowed": False,
        }


def run(config: Config, cid: str, job_id: str) -> dict[str, Any]:
    with execution_scheduler.admit(config, "heavy"):
        return _run(config, cid, job_id)


def _run(config: Config, cid: str, job_id: str) -> dict[str, Any]:
    with media_jobs.connection(config) as db:
        row, spec, source, subtitle = current(config, db, cid, job_id)
        if row["state"] != "queued":
            raise InvalidInput("media_processing_no_replay")
        db.execute(
            "UPDATE media_processing SET state='running',updated_at=? WHERE job_id=?",
            (time.time(), job_id),
        )

    def guard() -> None:
        with media_jobs.connection(config) as db:
            active, bound, _, _ = current(config, db, cid, job_id)
            if active["state"] != "running" or bound != spec:
                raise InvalidInput("media_processing_stopped")

    try:
        worker.execute(config, job_id, source, subtitle, spec, guard=guard)
        with media_jobs.connection(config) as db:
            active, bound, a, b = current(config, db, cid, job_id)
            if active["state"] != "running" or bound != spec:
                raise InvalidInput("media_processing_stopped")
            root = worker.directory(config, job_id)
            verify_processing(root / "input", root / "output", a, b, spec)
            sha = hashlib.sha256(read_processing(root / "output", "result.json")).hexdigest()
            db.execute(
                "UPDATE media_processing SET state='complete',result_sha256=?,updated_at=? "
                "WHERE job_id=?",
                (sha, time.time(), job_id),
            )
        return status(config, cid, job_id)
    except BaseException as error:
        with media_jobs.connection(config) as db:
            db.execute(
                "UPDATE media_processing SET state='failed',"
                "error_code='media_processing_attempt_failed',updated_at=? "
                "WHERE job_id=? AND state='running'",
                (time.time(), job_id),
            )
        raise error


def stop(config: Config, cid: str, job_id: str, *, recover: bool = False) -> dict[str, Any]:
    with media_jobs.connection(config) as db:
        media_glossary.access(config, db, cid, job_id)
        row = get(db, job_id)
        if row["state"] == "complete":
            raise InvalidInput("media_processing_terminal")
        spec = plan(json.loads(row["plan_json"]))
        state = "interrupted" if recover else "cancelled"
        db.execute(
            "UPDATE media_processing SET state=?,updated_at=? WHERE job_id=?",
            (state, time.time(), job_id),
        )
    if worker.directory(config, job_id).exists():
        worker.stop_owned(config, spec, job_id)
    return status(config, cid, job_id)
