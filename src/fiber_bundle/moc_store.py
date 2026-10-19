"""Bounded Vault-scoped MOC metadata and revision audit; caller authorizes and holds lock."""

import json
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass

from . import database, note_changes
from .config import Config, IntegrityError, contained, sync_directory
from .inputs import canonical, text

MAX_MOCS = 1000
MAX_MEMBERS = 1000
MAX_RECORD_BYTES = 256 * 1024
SCHEMA = """
CREATE TABLE mocs (
 vault_id TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL,
 record_json TEXT NOT NULL, PRIMARY KEY(vault_id,id)
) STRICT;
CREATE TABLE events (
 id INTEGER PRIMARY KEY, vault_id TEXT NOT NULL, moc_id TEXT NOT NULL,
 client_id TEXT NOT NULL, revision INTEGER NOT NULL, action TEXT NOT NULL,
 record_json TEXT NOT NULL, UNIQUE(vault_id,moc_id,revision)
) STRICT;
"""


@dataclass(frozen=True)
class Record:
    title: str
    vault_dev: int
    vault_ino: int
    revision: int
    members: dict[str, list[str]]


def stable_identity(value: str) -> None:
    if not isinstance(value, str) or not value.startswith(("note:", "publication:", "expert:")):
        raise IntegrityError("MOC requires a stable note identity")
    note_changes.identifier(value.split(":", 1)[1])


def validate(value: Record) -> None:
    text(value.title, limit=200)
    if (
        not isinstance(value.title, str)
        or not value.title.strip()
        or value.title != value.title.strip()
        or len(value.title) > 200
        or any(ord(c) < 32 or ord(c) == 127 for c in value.title)
        or any(type(v) is not int or v < 0 for v in (value.vault_dev, value.vault_ino))
        or type(value.revision) is not int
        or value.revision < 1
        or not isinstance(value.members, dict)
        or len(value.members) > MAX_MEMBERS
    ):
        raise IntegrityError("invalid MOC metadata")
    for identity, owners in value.members.items():
        stable_identity(identity)
        if not isinstance(owners, list) or owners not in (
            ["manual"],
            ["system"],
            ["manual", "system"],
        ):
            raise IntegrityError("invalid MOC ownership")
    if len(canonical(asdict(value)).encode()) > MAX_RECORD_BYTES:
        raise IntegrityError("MOC metadata exceeds limit")


@contextmanager
def connection(config: Config, *, write: bool = False) -> Iterator[sqlite3.Connection | None]:
    path = contained(config.state_root, "moc-associations.sqlite3")
    for suffix in ("-journal", "-wal", "-shm"):
        auxiliary = contained(config.state_root, "moc-associations.sqlite3" + suffix)
        if auxiliary.exists():
            info = auxiliary.stat(follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise IntegrityError("MOC auxiliary file is not independent")
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
            raise IntegrityError("MOC ledger is not a bounded independent file")
    finally:
        os.close(fd)
    db = sqlite3.connect(
        path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, isolation_level=None
    )
    db.row_factory = sqlite3.Row
    try:
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
            raise IntegrityError("unsupported MOC ledger")
        yield db
    finally:
        db.close()


def load(config: Config, vault_id: str, moc_id: str) -> Record | None:
    with connection(config) as db:
        if db is None:
            return None
        row = db.execute(
            "SELECT * FROM mocs WHERE vault_id=? AND id=?", (vault_id, moc_id)
        ).fetchone()
        if row is None:
            return None
        raw = row["record_json"]
        if not isinstance(raw, str) or len(raw.encode()) > MAX_RECORD_BYTES:
            raise IntegrityError("MOC record exceeds limit")
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or set(data) != {
                "title",
                "vault_dev",
                "vault_ino",
                "revision",
                "members",
            }:
                raise ValueError("invalid MOC record fields")
            value = Record(**data)
            validate(value)
            if canonical(asdict(value)) != raw or row["revision"] != value.revision:
                raise IntegrityError("MOC record is not canonical")
        except (ValueError, TypeError, RecursionError) as exc:
            raise IntegrityError("invalid MOC record") from exc
        return value


def save(
    config: Config,
    vault_id: str,
    moc_id: str,
    client_id: str,
    value: Record,
    action: str,
    expected_revision: int,
) -> None:
    validate(value)
    with connection(config, write=True) as db:
        assert db is not None
        with database.transaction(db):
            row = db.execute(
                "SELECT revision FROM mocs WHERE vault_id=? AND id=?", (vault_id, moc_id)
            ).fetchone()
            if (row[0] if row else 0) != expected_revision:
                raise IntegrityError("MOC revision conflict")
            if (
                row is None
                and db.execute(
                    "SELECT count(*) FROM mocs WHERE vault_id=?", (vault_id,)
                ).fetchone()[0]
                >= MAX_MOCS
            ):
                raise IntegrityError("MOC count limit exceeded")
            raw = canonical(asdict(value))
            db.execute(
                "INSERT INTO mocs VALUES(?,?,?,?) ON CONFLICT(vault_id,id) DO UPDATE SET "
                "revision=excluded.revision,record_json=excluded.record_json",
                (vault_id, moc_id, value.revision, raw),
            )
            db.execute(
                "INSERT INTO events(vault_id,moc_id,client_id,revision,action,record_json) "
                "VALUES(?,?,?,?,?,?)",
                (vault_id, moc_id, client_id, value.revision, action, raw),
            )


def next_revision(config: Config, vault: str, mid: str) -> int:
    with connection(config) as db:
        return (
            int(
                db.execute(
                    "SELECT coalesce(max(revision),0)+1 FROM events WHERE vault_id=? AND moc_id=?",
                    (vault, mid),
                ).fetchone()[0]
            )
            if db
            else 1
        )


def retire(config: Config, vault: str, mid: str, client: str, expected: int) -> None:
    """Remove a task-created active registration while retaining all revision events."""
    current = load(config, vault, mid)
    if current is None:
        return
    if current.revision != expected:
        raise IntegrityError("MOC revision conflict")
    with connection(config, write=True) as db:
        assert db is not None
        with database.transaction(db):
            db.execute(
                "INSERT INTO events(vault_id,moc_id,client_id,revision,action,record_json) "
                "VALUES(?,?,?,?,?,?)",
                (
                    vault,
                    mid,
                    client,
                    expected + 1,
                    "maintenance-cancel-retire",
                    canonical({**asdict(current), "revision": expected + 1}),
                ),
            )
            db.execute(
                "DELETE FROM mocs WHERE vault_id=? AND id=? AND revision=?", (vault, mid, expected)
            )
