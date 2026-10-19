"""Private metadata-only MOC dispatch queue and transactional phase audit."""

import hashlib
import json
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from . import database, note_changes
from .config import Config, IntegrityError, contained, sync_directory
from .inputs import canonical

MAX_JOBS = 1000
FIELDS = {
    "request_id",
    "client_id",
    "vault_id",
    "run_id",
    "publication_id",
    "moc_id",
    "revision",
    "path",
    "expected_sha256",
}
STATES = {"queued", "claimed", "failed", "applied", "not-applied", "unchanged"}
SCHEMA = """
CREATE TABLE jobs (
 id TEXT PRIMARY KEY, client_id TEXT NOT NULL, vault_id TEXT NOT NULL,
 request_json TEXT NOT NULL, plan_id TEXT, state TEXT NOT NULL
 CHECK(state IN ('queued','claimed','failed','applied','not-applied','unchanged'))
) STRICT;
CREATE TABLE events (
 id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
 state TEXT NOT NULL, UNIQUE(job_id,state)
) STRICT;
"""


@dataclass(frozen=True)
class Job:
    id: str
    request: dict[str, Any]
    plan_id: str | None
    state: str


def identity(request: dict[str, Any]) -> str:
    validate_request(request)
    return hashlib.sha256(canonical(request).encode()).hexdigest()


def validate_request(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise IntegrityError("invalid MOC dispatch request")
    for name in ("request_id", "client_id", "vault_id", "run_id", "publication_id", "moc_id"):
        if not isinstance(value[name], str):
            raise IntegrityError("invalid MOC dispatch scope identity")
        note_changes.identifier(value[name])
    if type(value["revision"]) is not int or value["revision"] < 1:
        raise IntegrityError("invalid MOC dispatch revision")
    expected = value["expected_sha256"]
    if expected != "absent" and not (
        isinstance(expected, str) and note_changes.DIGEST.fullmatch(expected)
    ):
        raise IntegrityError("invalid MOC dispatch expected hash")
    if not isinstance(value["path"], str):
        raise IntegrityError("invalid MOC dispatch path")
    note_changes.validate(
        [{"identity": "path:" + value["path"], "note_path": value["path"], "sha256": "0" * 64}]
    )
    if not value["path"].lower().endswith(".md"):
        raise IntegrityError("MOC dispatch target must be Markdown")
    if len(canonical(value).encode()) > 4096:
        raise IntegrityError("MOC dispatch request exceeds limit")


@contextmanager
def connection(config: Config, *, write: bool = False) -> Iterator[sqlite3.Connection | None]:
    path = contained(config.state_root, "moc-dispatch.sqlite3")
    for suffix in ("-journal", "-wal", "-shm"):
        auxiliary = contained(config.state_root, "moc-dispatch.sqlite3" + suffix)
        if auxiliary.exists():
            info = auxiliary.stat(follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise IntegrityError("MOC dispatch auxiliary file is not independent")
    new = write and not path.exists()
    if not write and not path.exists():
        yield None
        return
    if new:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        sync_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 64 * 1024 * 1024:
            raise IntegrityError("MOC dispatch ledger is not a bounded independent file")
    finally:
        os.close(fd)
    db = sqlite3.connect(
        path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, isolation_level=None
    )
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys=ON")
        if write:
            db.execute("PRAGMA synchronous=EXTRA")
            db.execute("PRAGMA fullfsync=ON")
        if new:
            with database.transaction(db):
                for statement in SCHEMA.split(";"):
                    if statement.strip():
                        db.execute(statement)
                db.execute("PRAGMA user_version=1")
        elif db.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise IntegrityError("unsupported MOC dispatch ledger")
        yield db
    finally:
        db.close()


def decode(row: sqlite3.Row) -> Job:
    try:
        raw = row["request_json"]
        if not isinstance(raw, str) or len(raw.encode()) > 4096:
            raise IntegrityError("MOC dispatch request exceeds limit")
        request = json.loads(raw)
        if canonical(request) != raw or identity(request) != row["id"]:
            raise IntegrityError("MOC dispatch request binding changed")
        if (request["client_id"], request["vault_id"]) != (row["client_id"], row["vault_id"]):
            raise IntegrityError("MOC dispatch scope changed")
        state, plan = row["state"], row["plan_id"]
        if state not in STATES or (state == "unchanged") != (plan is None):
            raise IntegrityError("invalid MOC dispatch phase")
        if plan is not None and not (isinstance(plan, str) and note_changes.DIGEST.fullmatch(plan)):
            raise IntegrityError("invalid MOC dispatch plan")
        return Job(row["id"], request, plan, state)
    except (ValueError, TypeError, RecursionError) as exc:
        raise IntegrityError("invalid MOC dispatch record") from exc


def jobs(config: Config, client_id: str, vault_id: str) -> list[Job]:
    with connection(config) as db:
        if db is None:
            return []
        rows = db.execute(
            "SELECT * FROM jobs WHERE client_id=? AND vault_id=? ORDER BY rowid LIMIT ?",
            (client_id, vault_id, MAX_JOBS + 1),
        ).fetchall()
    if len(rows) > MAX_JOBS:
        raise IntegrityError("MOC dispatch job limit exceeded")
    return [decode(row) for row in rows]


def create(config: Config, request: dict[str, Any], plan_id: str | None) -> Job:
    if plan_id is not None and not (
        isinstance(plan_id, str) and note_changes.DIGEST.fullmatch(plan_id)
    ):
        raise IntegrityError("invalid MOC dispatch plan")
    job = Job(identity(request), request, plan_id, "unchanged" if plan_id is None else "queued")
    with connection(config, write=True) as db:
        assert db is not None
        with database.transaction(db):
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job.id,)).fetchone()
            if row is not None:
                existing = decode(row)
                if existing.request != request or existing.plan_id != plan_id:
                    raise IntegrityError("MOC dispatch request already bound")
                return existing
            if (
                db.execute(
                    "SELECT count(*) FROM jobs WHERE client_id=? AND vault_id=?",
                    (request["client_id"], request["vault_id"]),
                ).fetchone()[0]
                >= MAX_JOBS
            ):
                raise IntegrityError("MOC dispatch job limit exceeded")
            db.execute(
                "INSERT INTO jobs VALUES(?,?,?,?,?,?)",
                (
                    job.id,
                    request["client_id"],
                    request["vault_id"],
                    canonical(request),
                    plan_id,
                    job.state,
                ),
            )
            db.execute("INSERT INTO events(job_id,state) VALUES(?,?)", (job.id, job.state))
    return job


def transition(config: Config, job: Job, state: str) -> Job:
    allowed = {
        "queued": {"claimed"},
        "claimed": {"failed", "applied", "not-applied"},
        "failed": {"applied", "not-applied"},
    }
    if state not in allowed.get(job.state, set()):
        raise IntegrityError("invalid MOC dispatch transition")
    with connection(config, write=True) as db:
        assert db is not None
        with database.transaction(db):
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job.id,)).fetchone()
            if row is None or decode(row) != job:
                raise IntegrityError("MOC dispatch phase conflict")
            db.execute("UPDATE jobs SET state=? WHERE id=?", (state, job.id))
            db.execute("INSERT INTO events(job_id,state) VALUES(?,?)", (job.id, state))
    return Job(job.id, job.request, job.plan_id, state)
