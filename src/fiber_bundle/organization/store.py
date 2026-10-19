"""Private SQLite JSON ledger. Callers hold Fiber's storage lock; no main-schema migration."""

import json
import os
import sqlite3
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from ..config import Config, IntegrityError, contained, sync_directory
from ..inputs import canonical

TABLES = {
    "packages",
    "package_scans",
    "versions",
    "policies",
    "entities",
    "decisions",
    "jobs",
    "commands",
    "mocs",
    "triggers",
    "vectors",
    "model_reviews",
    "note_moc",
    "analysis_cache",
    "maintenance_undo",
    "manual_resolutions",
}


@contextmanager
def connection(config: Config, *, write: bool = False) -> Iterator[sqlite3.Connection | None]:
    path = contained(config.state_root, "organization.sqlite3")
    if not path.exists() and not write:
        yield None
        return
    new = not path.exists()
    if new:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        sync_directory(path.parent)
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = path.with_name(path.name + suffix)
        if p.is_symlink():
            raise IntegrityError("organization_ledger_invalid")
        if p.exists():
            s = p.lstat()
            if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1:
                raise IntegrityError("organization_ledger_invalid")
    db = sqlite3.connect(
        path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, isolation_level=None
    )
    db.row_factory = sqlite3.Row
    try:
        if write:
            db.execute("PRAGMA synchronous=EXTRA")
            db.execute("PRAGMA fullfsync=ON")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in ({0, 1} if write else {1}):
            raise IntegrityError("organization_ledger_version")
        if write:
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master")}
            missing = TABLES - names
            indexes = {t for t in TABLES if t + "_vault" not in names}
        if write and (missing or indexes or version == 0):
            db.execute("BEGIN IMMEDIATE")
            for table in missing:
                db.execute(
                    f"CREATE TABLE IF NOT EXISTS {table} "
                    "(id TEXT PRIMARY KEY, vault TEXT NOT NULL, value TEXT NOT NULL, "
                    "updated REAL NOT NULL) STRICT"
                )
            for table in indexes:
                db.execute(f"CREATE INDEX IF NOT EXISTS {table}_vault ON {table}(vault)")
            db.execute("PRAGMA user_version=1")
            db.execute("COMMIT")
        if db.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise IntegrityError("organization_ledger_version")
        yield db
    finally:
        db.close()


def get(config: Config, table: Any, identity: str) -> Any:
    assert table in TABLES
    with connection(config) as db:
        if db and not db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
            return None
        row = (
            db.execute(f"SELECT value FROM {table} WHERE id=?", (identity,)).fetchone()
            if db
            else None
        )
        return json.loads(row[0]) if row else None


def rows(config: Config, table: Any, vault: str | None = None) -> list[dict[str, Any]]:
    assert table in TABLES
    with connection(config) as db:
        if db is None:
            return []
        query = f"SELECT value FROM {table}"
        params: tuple[str, ...] = ()
        if vault is not None:
            query += " WHERE vault=?"
            params = (vault,)
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
            return []
        return [json.loads(r[0]) for r in db.execute(query + " ORDER BY id", params)]


def put(config: Config, table: Any, identity: str, vault: str, value: Any) -> None:
    put_many(config, table, vault, {identity: value})


def get_many(config: Config, table: Any, identities: list[str]) -> dict[str, Any]:
    assert table in TABLES
    result: dict[str, Any] = {}
    with connection(config) as db:
        if (
            db is None
            or not db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone()
        ):
            return result
        for start in range(0, len(identities), 200):
            keys = identities[start : start + 200]
            placeholders = ",".join("?" for _ in keys)
            for row in db.execute(
                f"SELECT id,value FROM {table} WHERE id IN ({placeholders})", keys
            ):
                result[row[0]] = json.loads(row[1])
    return result


def put_many(config: Config, table: Any, vault: str, values: dict[str, Any]) -> None:
    assert table in TABLES
    if not values:
        return
    with connection(config, write=True) as db:
        assert db is not None
        with db:
            db.execute("BEGIN IMMEDIATE")
            db.executemany(
                f"INSERT INTO {table} VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "vault=excluded.vault,value=excluded.value,updated=excluded.updated",
                [
                    (identity, vault, canonical(value), time.time())
                    for identity, value in values.items()
                ],
            )

    if table == "jobs":
        from ..backup_automation.events import organization

        organization(config, values)
