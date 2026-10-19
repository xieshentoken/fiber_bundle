"""Private independent graph ledger. Caller holds host storage lock; no SDK imports."""

import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from .. import database
from ..config import Config, IntegrityError, contained, sync_directory
from ..inputs import canonical
from .contracts import MAX_INPUT_BYTES, decode_request, object_fields, read_json
from .intake import Binding, Prepared

MAX_JOBS = 32
SCHEMA = """
CREATE TABLE jobs (
 id TEXT PRIMARY KEY, client_id TEXT NOT NULL, vault_id TEXT NOT NULL,
 idempotency_key TEXT NOT NULL UNIQUE, profile_json TEXT NOT NULL, prepared_json TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN
 ('queued','running','completed','cancelled','interrupted','stale','failed')),
 error_code TEXT, result_json TEXT, vectors_json TEXT, embedded_count INTEGER,
 created_at REAL NOT NULL, finished_at REAL
) STRICT;
CREATE TABLE generations (
 client_id TEXT NOT NULL, vault_id TEXT NOT NULL, job_id TEXT NOT NULL REFERENCES jobs(id),
 state TEXT NOT NULL CHECK(state IN ('ready','stale')), PRIMARY KEY(client_id,vault_id)
) STRICT;
CREATE TABLE reviews (
 client_id TEXT NOT NULL, vault_id TEXT NOT NULL, configuration TEXT NOT NULL,
 object_id TEXT NOT NULL, revision INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('accepted','rejected','suggested')),
 reason TEXT NOT NULL, reviewed_at REAL NOT NULL,
 PRIMARY KEY(client_id,vault_id,configuration,object_id,revision)
) STRICT;
CREATE TABLE writebacks (
 id TEXT PRIMARY KEY, client_id TEXT NOT NULL, vault_id TEXT NOT NULL,
 object_id TEXT NOT NULL, request_json TEXT NOT NULL, state TEXT NOT NULL,
 result_json TEXT
) STRICT;
"""


@contextmanager
def connection(config: Config, *, write: bool = False) -> Iterator[sqlite3.Connection | None]:
    path = contained(config.state_root, "knowledge-graph.sqlite3")
    if not write and not path.exists():
        yield None
        return
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = contained(config.state_root, "knowledge-graph.sqlite3" + suffix)
        if candidate.exists() or candidate.is_symlink():
            info = candidate.stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_size > 256 * 1024 * 1024
            ):
                raise IntegrityError("graph_ledger_file_invalid")
    new = not path.exists()
    if new:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        sync_directory(path.parent)
    db = sqlite3.connect(
        path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, isolation_level=None
    )
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys=ON")
        if write:
            db.execute("PRAGMA synchronous=EXTRA")
            db.execute("PRAGMA fullfsync=ON")
            page_bytes = db.execute("PRAGMA page_size").fetchone()[0]
            db.execute(f"PRAGMA max_page_count={256 * 1024 * 1024 // page_bytes}")
        if new:
            with database.transaction(db):
                for statement in SCHEMA.split(";"):
                    if statement.strip():
                        db.execute(statement)
                db.execute("PRAGMA user_version=1")
        elif db.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise IntegrityError("graph_ledger_schema_unsupported")
        yield db
    finally:
        db.close()


def encode(prepared: Prepared) -> str:
    from dataclasses import asdict

    return canonical(
        {
            "request": read_json(prepared.request.encode(), MAX_INPUT_BYTES),
            "bindings": [asdict(b) for b in prepared.bindings],
            "permission_epoch": prepared.permission_epoch,
        }
    )


def decode(raw: str) -> Prepared:
    value = object_fields(
        read_json(raw.encode(), MAX_INPUT_BYTES + 65536),
        {"request", "bindings", "permission_epoch"},
    )
    request = decode_request(canonical(value["request"]).encode())
    if (
        type(value["permission_epoch"]) is not int
        or value["permission_epoch"] < 1
        or not isinstance(value["bindings"], list)
    ):
        raise IntegrityError("graph_invalid_host_binding")
    bindings = []
    for item in value["bindings"]:
        item = object_fields(item, set(Binding.__dataclass_fields__))
        if (
            not isinstance(item["file_key"], list)
            or len(item["file_key"]) != 3
            or not isinstance(item["vault_key"], list)
            or len(item["vault_key"]) != 2
        ):
            raise IntegrityError("graph_invalid_host_binding")
        bindings.append(
            Binding(
                item["note_id"],
                item["relative_path"],
                tuple(item["file_key"]),
                tuple(item["vault_key"]),
            )
        )
    if tuple(b.note_id for b in bindings) != tuple(n.note_id for n in request.notes):
        raise IntegrityError("graph_invalid_host_binding")
    return Prepared(request, tuple(bindings), value["permission_epoch"])


def job(db: sqlite3.Connection, jid: str, cid: str, vid: str) -> sqlite3.Row:
    from ..auth import Denied

    row = db.execute(
        "SELECT * FROM jobs WHERE id=? AND client_id=? AND vault_id=?", (jid, cid, vid)
    ).fetchone()
    if row is None:
        raise Denied(404)
    assert isinstance(row, sqlite3.Row)
    return row


def view(row: sqlite3.Row) -> dict[str, Any]:
    return {
        k: row[k]
        for k in ("id", "state", "error_code", "created_at", "finished_at", "embedded_count")
    }
