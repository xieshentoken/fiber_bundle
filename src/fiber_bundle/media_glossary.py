"""Immutable task-local terminology with exact revision confirmation and live authorization."""

import hashlib
import re
import sqlite3
import time
from typing import Any

from . import database, media_bindings, media_jobs
from .config import Config, IntegrityError, InvalidInput
from .media_files import canonical, decode
from .media_glossary_contract import MAX_REVISIONS, ambiguities, load, validate


def access(
    config: Config, db: sqlite3.Connection, client_id: str, job_id: str, *, permission: str = "add"
) -> sqlite3.Row:
    row = media_jobs.load(db, client_id, job_id)
    media_bindings.authorize(config, db, client_id, media_jobs.binding(row), permission=permission)
    return row


def latest(db: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
    row = db.execute(
        "SELECT * FROM media_glossary_revisions WHERE job_id=? ORDER BY revision DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    return row if isinstance(row, sqlite3.Row) else None


def verified(row: sqlite3.Row) -> dict[str, Any]:
    raw = row["raw_json"].encode()
    snapshot = validate(decode(row["snapshot_json"].encode()))
    if (
        load(raw) != snapshot
        or hashlib.sha256(raw).hexdigest() != row["raw_sha256"]
        or (hashlib.sha256(canonical(snapshot)).hexdigest() != row["snapshot_sha256"])
    ):
        raise IntegrityError("media_glossary_snapshot_changed")
    return snapshot


def gate(db: sqlite3.Connection, job: sqlite3.Row) -> dict[str, Any]:
    row = latest(db, job["id"])
    reasons = []
    if job["state"] not in {"queued", "complete"}:
        reasons.append("media_task_" + job["state"])
    confirmation = None
    issues: list[dict[str, Any]] = []
    if row is None:
        reasons.append("glossary_missing")
    else:
        snapshot = verified(row)
        issues = ambiguities(snapshot)
        if issues:
            reasons.append("glossary_ambiguous")
        confirmation = db.execute(
            "SELECT * FROM media_glossary_confirmations WHERE job_id=? AND revision=?",
            (job["id"], row["revision"]),
        ).fetchone()
        if confirmation is not None and (
            confirmation["snapshot_sha256"] != row["snapshot_sha256"]
            or confirmation["client_id"] != job["client_id"]
            or (not snapshot["terms"] and not confirmation["allow_empty"])
        ):
            raise IntegrityError("media_glossary_confirmation_changed")
        if confirmation is None:
            reasons.append("glossary_confirmation_required")
    return {
        "job_id": job["id"],
        "revision": row["revision"] if row else None,
        "snapshot_sha256": row["snapshot_sha256"] if row else None,
        "state": "blocked" if reasons else "confirmed",
        "blocking_reasons": reasons,
        "unresolved_count": len(issues),
        "confirmation_recorded": confirmation is not None,
        "terminology_gate_passed": not reasons,
        "semantic_review_verified": False,
        "asr_ready": False,
        "asr_started": False,
        "publication_ready": False,
        "cleanup_allowed": False,
    }


def status(config: Config, client_id: str, job_id: str) -> dict[str, Any]:
    with media_jobs.connection(config) as db:
        return gate(db, access(config, db, client_id, job_id))


def show(config: Config, client_id: str, job_id: str) -> dict[str, Any]:
    with media_jobs.connection(config) as db:
        return show_locked(config, db, client_id, job_id)


def show_locked(
    config: Config, db: sqlite3.Connection, client_id: str, job_id: str
) -> dict[str, Any]:
    """Caller owns the storage lock through HTTP delivery or another trusted entry point."""
    job = access(config, db, client_id, job_id, permission="query")
    row = latest(db, job_id)
    snapshot = verified(row) if row is not None else None
    return {
        **gate(db, job),
        "snapshot": snapshot,
        "ambiguities": ambiguities(snapshot) if snapshot else [],
    }


def mutable(job: sqlite3.Row) -> None:
    if job["state"] not in {"queued", "complete"}:
        raise InvalidInput("media_glossary_task_stopped")


def freeze(config: Config, client_id: str, job_id: str, raw: bytes) -> dict[str, Any]:
    with media_jobs.connection(config) as db:
        return freeze_locked(config, db, client_id, job_id, raw)


def freeze_locked(
    config: Config, db: sqlite3.Connection, client_id: str, job_id: str, raw: bytes
) -> dict[str, Any]:
    """Preserve submitted bytes without reacquiring an entry point's storage lock."""
    snapshot = load(raw)
    encoded = canonical(snapshot)
    sha = hashlib.sha256(encoded).hexdigest()
    job = access(config, db, client_id, job_id)
    mutable(job)
    current = latest(db, job_id)
    if current is not None:
        verified(current)
        if sha == current["snapshot_sha256"]:
            return gate(db, job)
    revision = current["revision"] + 1 if current else 1
    if revision > MAX_REVISIONS:
        raise InvalidInput("media_glossary_revision_limit")
    with database.transaction(db):
        db.execute(
            "INSERT INTO media_glossary_revisions VALUES(?,?,?,?,?,?,?)",
            (
                job_id,
                revision,
                sha,
                hashlib.sha256(raw).hexdigest(),
                encoded.decode(),
                raw.decode(),
                time.time(),
            ),
        )
    return gate(db, job)


def confirm(
    config: Config,
    client_id: str,
    job_id: str,
    revision: int,
    snapshot_sha256: str,
    *,
    allow_empty: bool = False,
) -> dict[str, Any]:
    with media_jobs.connection(config) as db:
        return confirm_locked(
            config, db, client_id, job_id, revision, snapshot_sha256, allow_empty=allow_empty
        )


def confirm_locked(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    job_id: str,
    revision: int,
    snapshot_sha256: str,
    *,
    allow_empty: bool = False,
) -> dict[str, Any]:
    """Caller owns the project storage lock; supports exact trusted channel confirmation."""
    if (
        type(revision) is not int
        or not 1 <= revision <= MAX_REVISIONS
        or (
            not isinstance(snapshot_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", snapshot_sha256) is None
        )
        or type(allow_empty) is not bool
    ):
        raise InvalidInput("media_glossary_confirmation_input")
    job = access(config, db, client_id, job_id)
    mutable(job)
    row = latest(db, job_id)
    if row is None or (row["revision"], row["snapshot_sha256"]) != (revision, snapshot_sha256):
        raise InvalidInput("media_glossary_confirmation_stale")
    snapshot = verified(row)
    if ambiguities(snapshot):
        raise InvalidInput("media_glossary_confirmation_ambiguous")
    if not snapshot["terms"] and not allow_empty:
        raise InvalidInput("media_glossary_explicit_empty_review_required")
    with database.transaction(db):
        db.execute(
            "INSERT OR IGNORE INTO media_glossary_confirmations VALUES(?,?,?,?,?,?)",
            (job_id, revision, snapshot_sha256, client_id, int(allow_empty), time.time()),
        )
    return gate(db, job)
