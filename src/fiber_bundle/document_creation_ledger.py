"""Private document intent/version sidecar; caller holds the main storage lock."""

import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager

from . import database
from . import document_creation_contract as contract
from .config import Config, IntegrityError, InvalidInput, contained, sync_directory
from .inputs import canonical
from .objects import digest

MAX_TASKS = 1000
MAX_LEDGER_BYTES = 64 * 1024 * 1024
SCHEMA = """
CREATE TABLE tasks (
 id TEXT PRIMARY KEY, client_id TEXT NOT NULL, submission_id TEXT NOT NULL,
 submission_sha256 TEXT NOT NULL, query_task_id TEXT NOT NULL, input_sha256 TEXT NOT NULL,
 head_revision INTEGER NOT NULL CHECK(head_revision BETWEEN 1 AND 32),
 head_sha256 TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('prepared','cancelled')),
 created_at REAL NOT NULL CHECK(created_at>0), updated_at REAL NOT NULL CHECK(updated_at>0),
 UNIQUE(client_id,submission_id)
) STRICT;
CREATE TABLE versions (
 task_id TEXT NOT NULL REFERENCES tasks(id),
 revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 32),
 plan_sha256 TEXT NOT NULL, plan_json TEXT NOT NULL CHECK(json_valid(plan_json)),
 created_at REAL NOT NULL CHECK(created_at>0), PRIMARY KEY(task_id,revision)
) STRICT;
CREATE TABLE events (
 id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 action TEXT NOT NULL CHECK(action IN ('submitted','amended','cancelled')),
 revision INTEGER NOT NULL, plan_sha256 TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('prepared','cancelled')),
 created_at REAL NOT NULL CHECK(created_at>0), UNIQUE(task_id,action,revision),
 FOREIGN KEY(task_id,revision) REFERENCES versions(task_id,revision)
) STRICT;
"""


@contextmanager
def connection(
    config: Config, *, write: bool = False, create: bool = False
) -> Iterator[sqlite3.Connection | None]:
    path = contained(config.state_root, "document-creation.sqlite3")
    for suffix in ("-journal", "-wal", "-shm"):
        contained(config.state_root, "document-creation.sqlite3" + suffix)
    new = write and create and not path.exists()
    if not new and not path.exists():
        yield None
        return
    if new:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        sync_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > MAX_LEDGER_BYTES
        ):
            raise IntegrityError("document_creation_ledger_invalid")
    finally:
        os.close(fd)
    db = sqlite3.connect(
        path.as_uri() + ("?mode=rw" if write else "?mode=ro"),
        uri=True,
        isolation_level=None,
        timeout=5,
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
            raise IntegrityError("document_creation_ledger_invalid")
        yield db
    finally:
        db.close()


def submission(query_task_id: str, requirements: str, requested_format: str) -> str:
    return digest(
        canonical(
            {
                "query_task_id": query_task_id,
                "requirements": requirements,
                "requested_format": requested_format,
            }
        ).encode()
    )


def versions(db: sqlite3.Connection, task: sqlite3.Row) -> tuple[contract.CreationPlan, ...]:
    """Verify every stored revision and its matching audit event before exposing a head."""
    try:
        rows = db.execute(
            "SELECT * FROM versions WHERE task_id=? ORDER BY revision LIMIT ?",
            (task["id"], contract.MAX_REVISIONS + 1),
        ).fetchall()
        if not rows or len(rows) != task["head_revision"]:
            raise ValueError()
        plans: list[contract.CreationPlan] = []
        for number, row in enumerate(rows, 1):
            plan = contract.parse(row["plan_json"])
            if (
                row["revision"] != number
                or plan.revision != number
                or row["plan_sha256"] != plan.sha256
                or plan.apply_at != "before_execution"
                or plan.client_id != task["client_id"]
                or plan.query_task_id != task["query_task_id"]
                or digest(plan.input_json.encode()) != task["input_sha256"]
                or (
                    plans
                    and (
                        plan.input_json != plans[0].input_json
                        or plan.requested_format != plans[0].requested_format
                        or plan.requirements[:-1] != plans[-1].requirements
                        or plan.parent_sha256 != plans[-1].sha256
                    )
                )
            ):
                raise ValueError()
            plans.append(plan)
        if (
            plans[-1].sha256 != task["head_sha256"]
            or submission(
                plans[0].query_task_id, plans[0].requirements[0], plans[0].requested_format
            )
            != task["submission_sha256"]
        ):
            raise ValueError()
        events = db.execute(
            "SELECT * FROM events WHERE task_id=? ORDER BY id LIMIT ?",
            (task["id"], contract.MAX_REVISIONS + 2),
        ).fetchall()
        expected = [
            ("submitted" if i == 1 else "amended", i, p.sha256, "prepared")
            for i, p in enumerate(plans, 1)
        ]
        if task["state"] == "cancelled":
            expected.append(("cancelled", len(plans), plans[-1].sha256, "cancelled"))
        elif task["state"] != "prepared":
            raise ValueError()
        if [(e["action"], e["revision"], e["plan_sha256"], e["state"]) for e in events] != expected:
            raise ValueError()
        return tuple(plans)
    except (KeyError, TypeError, ValueError, InvalidInput):
        raise IntegrityError("document_creation_ledger_invalid") from None


def append(
    db: sqlite3.Connection, task_id: str, plan: contract.CreationPlan, now: float, action: str
) -> None:
    wire = canonical(plan.value())
    if len(wire.encode()) > contract.MAX_PLAN_BYTES:
        raise IntegrityError("document_creation_ledger_invalid")
    db.execute(
        "INSERT INTO versions VALUES(?,?,?,?,?)", (task_id, plan.revision, plan.sha256, wire, now)
    )
    event(db, task_id, plan, now, action, "prepared")


def event(
    db: sqlite3.Connection,
    task_id: str,
    plan: contract.CreationPlan,
    now: float,
    action: str,
    state: str,
) -> None:
    db.execute(
        "INSERT INTO events(task_id,action,revision,plan_sha256,state,created_at) "
        "VALUES(?,?,?,?,?,?)",
        (task_id, action, plan.revision, plan.sha256, state, now),
    )
