"""Vault-scoped sidecar identity ledger. Caller holds the main storage lock and authorizes."""

import math
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from . import database
from .config import Config, IntegrityError, contained, sync_directory
from .inputs import canonical

MAX_REGISTRATIONS = 10000
SCHEMA = """
CREATE TABLE registrations (
 vault_id TEXT NOT NULL, identity TEXT NOT NULL, publication_id TEXT,
 relative_path TEXT NOT NULL, sha256 TEXT NOT NULL,
 file_dev INTEGER NOT NULL, file_ino INTEGER NOT NULL, file_birth TEXT NOT NULL,
 vault_dev INTEGER NOT NULL, vault_ino INTEGER NOT NULL,
 revision INTEGER NOT NULL CHECK(revision>0),
 PRIMARY KEY(vault_id,identity), UNIQUE(vault_id,file_dev,file_ino,file_birth)
) STRICT;
CREATE TABLE events (
 id INTEGER PRIMARY KEY, vault_id TEXT NOT NULL, identity TEXT NOT NULL,
 client_id TEXT NOT NULL, revision INTEGER NOT NULL, registration_json TEXT NOT NULL,
 UNIQUE(vault_id,identity,revision)
) STRICT;
"""


@dataclass(frozen=True)
class Registration:
    identity: str
    publication_id: str | None
    relative_path: str
    sha256: str
    file_key: tuple[int, int, str]
    vault_key: tuple[int, int]
    revision: int


@contextmanager
def connection(config: Config, *, write: bool = False) -> Iterator[sqlite3.Connection | None]:
    path = contained(config.state_root, "note-identities.sqlite3")
    for suffix in ("-journal", "-wal", "-shm"):
        contained(config.state_root, "note-identities.sqlite3" + suffix)
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
            raise IntegrityError("note identity ledger is not a bounded independent file")
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
            raise IntegrityError("unsupported note identity ledger")
        yield db
    finally:
        db.close()


def records(config: Config, vault_id: str) -> list[Registration]:
    with connection(config) as db:
        if db is None:
            return []
        rows = db.execute(
            "SELECT * FROM registrations WHERE vault_id=? ORDER BY identity LIMIT ?",
            (vault_id, MAX_REGISTRATIONS + 1),
        ).fetchall()
    if len(rows) > MAX_REGISTRATIONS:
        raise IntegrityError("note identity registration limit exceeded")
    result = []
    for row in rows:
        try:
            birth = float.fromhex(row["file_birth"])
            if not math.isfinite(birth) or birth.hex() != row["file_birth"]:
                raise ValueError("invalid file birth")
        except (ValueError, TypeError) as exc:
            raise IntegrityError("invalid note identity ledger record") from exc
        result.append(
            Registration(
                row["identity"],
                row["publication_id"],
                row["relative_path"],
                row["sha256"],
                (row["file_dev"], row["file_ino"], row["file_birth"]),
                (row["vault_dev"], row["vault_ino"]),
                row["revision"],
            )
        )
    return result


def next_revision(config: Config, vault: str, identity: str) -> int:
    """An undone registration keeps its events; never reuse a historical revision."""
    with connection(config) as db:
        return (
            int(
                db.execute(
                    "SELECT coalesce(max(revision),0)+1 FROM events "
                    "WHERE vault_id=? AND identity=?",
                    (vault, identity),
                ).fetchone()[0]
            )
            if db
            else 1
        )


def save(
    config: Config,
    vault_id: str,
    client_id: str,
    value: Registration,
    *,
    expected_revision: int | None = None,
) -> None:
    with connection(config, write=True) as db:
        assert db is not None
        with database.transaction(db):
            row = db.execute(
                "SELECT revision FROM registrations WHERE vault_id=? AND identity=?",
                (vault_id, value.identity),
            ).fetchone()
            if (row[0] if row else None) != expected_revision:
                raise IntegrityError("note identity revision conflict")
            if (
                row is None
                and db.execute(
                    "SELECT count(*) FROM registrations WHERE vault_id=?", (vault_id,)
                ).fetchone()[0]
                >= MAX_REGISTRATIONS
            ):
                raise IntegrityError("note identity registration limit exceeded")
            db.execute(
                "INSERT INTO registrations VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(vault_id,identity) DO UPDATE SET relative_path=excluded.relative_path,"
                "sha256=excluded.sha256,file_dev=excluded.file_dev,file_ino=excluded.file_ino,"
                "file_birth=excluded.file_birth,revision=excluded.revision",
                (
                    vault_id,
                    value.identity,
                    value.publication_id,
                    value.relative_path,
                    value.sha256,
                    *value.file_key,
                    *value.vault_key,
                    value.revision,
                ),
            )
            db.execute(
                "INSERT INTO events(vault_id,identity,client_id,revision,registration_json) "
                "VALUES(?,?,?,?,?)",
                (
                    vault_id,
                    value.identity,
                    client_id,
                    value.revision,
                    canonical(
                        {
                            "path": value.relative_path,
                            "sha256": value.sha256,
                            "file_key": value.file_key,
                            "publication_id": value.publication_id,
                        }
                    ),
                ),
            )


def transfer(
    config: Config,
    source: str,
    target: str,
    client: str,
    before: Registration,
    target_path: str,
    target_key: tuple[int, int],
    execution: str,
    *,
    sha256: str | None = None,
    file_key: tuple[int, int, str] | None = None,
) -> Registration:
    """Move one registration and append both audit events in a single transaction."""
    from dataclasses import replace

    from . import note_identities

    note_identities.validate(before)
    current_sha = sha256 if sha256 is not None else before.sha256
    target_file = file_key if file_key is not None else before.file_key
    audit = canonical(
        {
            "action": "migrate",
            "execution_id": execution,
            "source": source,
            "target": target,
            "path": target_path,
            "sha256": current_sha,
            "file_key": target_file,
            "publication_id": before.publication_id,
        }
    )
    with connection(config, write=True) as db:
        assert db is not None
        with database.transaction(db):
            source_records = records(config, source)
            target_records = records(config, target)
            current = next((r for r in source_records if r.identity == before.identity), None)
            existing = next((r for r in target_records if r.identity == before.identity), None)
            if current is None and existing is not None:
                event = db.execute(
                    "SELECT registration_json FROM events WHERE vault_id=? AND identity=? "
                    "AND revision=?",
                    (target, before.identity, existing.revision),
                ).fetchone()
                if (
                    event
                    and event[0] == audit
                    and existing.relative_path == target_path
                    and existing.sha256 == current_sha
                    and existing.file_key == target_file
                    and existing.vault_key == target_key
                    and existing.publication_id == before.publication_id
                ):
                    return existing
            if current != before or existing is not None:
                raise IntegrityError("migration identity transfer conflict")
            if len(target_records) >= MAX_REGISTRATIONS:
                raise IntegrityError("note identity registration limit exceeded")
            revision = (
                db.execute(
                    "SELECT max(revision) FROM events WHERE identity=?", (before.identity,)
                ).fetchone()[0]
                + 1
            )
            after = replace(
                before,
                relative_path=target_path,
                vault_key=target_key,
                revision=revision,
                sha256=current_sha,
                file_key=target_file,
            )
            note_identities.validate(after)
            db.execute(
                "DELETE FROM registrations WHERE vault_id=? AND identity=?",
                (source, before.identity),
            )
            db.execute(
                "INSERT INTO registrations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    target,
                    after.identity,
                    before.publication_id,
                    after.relative_path,
                    after.sha256,
                    *after.file_key,
                    *after.vault_key,
                    after.revision,
                ),
            )
            for vault in (source, target):
                db.execute(
                    "INSERT INTO events(vault_id,identity,client_id,revision,registration_json) "
                    "VALUES(?,?,?,?,?)",
                    (vault, after.identity, client, revision, audit),
                )
            return after
