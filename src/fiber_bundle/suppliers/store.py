"""Lazy private sidecar. Callers hold the shared storage lease for mutations."""

import json
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from ..config import Config, IntegrityError, contained, sync_directory
from ..inputs import canonical

NAME = "supplier-intelligence.sqlite3"
KINDS = frozenset(
    {
        "supplier",
        "alias",
        "contact",
        "product",
        "quote",
        "source",
        "issue",
        "job",
        "correction",
        "snapshot",
        "history",
        "model_call",
        "activation",
    }
)


@contextmanager
def connection(config: Config, *, write: bool = False) -> Iterator[sqlite3.Connection | None]:
    path = contained(config.state_root, NAME)
    if not path.exists() and not write:
        yield None
        return
    if not path.exists():
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        sync_directory(path.parent)
    for suffix in ("", "-wal", "-shm", "-journal"):
        target = path.with_name(path.name + suffix)
        if target.is_symlink() or (
            target.exists()
            and (not stat.S_ISREG(target.lstat().st_mode) or target.lstat().st_nlink != 1)
        ):
            raise IntegrityError("supplier_storage_invalid")
    db = sqlite3.connect(path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True)
    db.row_factory = sqlite3.Row
    try:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in ({0, 1} if write else {1}):
            raise IntegrityError("supplier_storage_version")
        if write:
            db.execute("PRAGMA synchronous=EXTRA")
            db.execute("PRAGMA fullfsync=ON")
            if not version:
                db.executescript("""
                CREATE TABLE profiles(vault TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                    value TEXT NOT NULL) STRICT;
                CREATE TABLE records(vault TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
                    value TEXT NOT NULL, PRIMARY KEY(vault,kind,id)) STRICT;
                CREATE TABLE operations(vault TEXT NOT NULL, id TEXT NOT NULL, digest TEXT NOT NULL,
                    result TEXT NOT NULL, PRIMARY KEY(vault,id)) STRICT;
                PRAGMA user_version=1;
                """)
            db.execute("BEGIN IMMEDIATE")
        yield db
        if write:
            db.commit()
    except BaseException:
        if write:
            db.rollback()
        raise
    finally:
        db.close()


def rows(config: Config, vault: str, kind: str) -> list[dict[str, Any]]:
    assert kind in KINDS
    with connection(config) as db:
        if db is None:
            return []
        values = db.execute(
            "SELECT value FROM records WHERE vault=? AND kind=? ORDER BY id LIMIT 100001",
            (vault, kind),
        ).fetchall()
        if len(values) > 100000:
            raise IntegrityError("supplier_scope_limit")
        return [json.loads(row[0]) for row in values]


def get(config: Config, vault: str, kind: str, identity: str) -> dict[str, Any] | None:
    assert kind in KINDS
    with connection(config) as db:
        row = (
            db.execute(
                "SELECT value FROM records WHERE vault=? AND kind=? AND id=?",
                (vault, kind, identity),
            ).fetchone()
            if db
            else None
        )
        return json.loads(row[0]) if row else None


def put(db: sqlite3.Connection, vault: str, kind: str, value: dict[str, Any]) -> None:
    assert kind in KINDS
    old = db.execute(
        "SELECT value FROM records WHERE vault=? AND kind=? AND id=?", (vault, kind, value["id"])
    ).fetchone()
    if old and kind in {"supplier", "product", "quote", "contact"}:
        previous = json.loads(old[0])
        if previous.get("user_revision", 0) > value.get("user_revision", 0):
            value = {
                **value,
                **{
                    k: previous[k]
                    for k in ("overrides", "user_revision", "correction_ids")
                    if k in previous
                },
            }
    db.execute(
        "INSERT INTO records VALUES(?,?,?,?) ON CONFLICT(vault,kind,id) "
        "DO UPDATE SET value=excluded.value",
        (vault, kind, value["id"], canonical(value)),
    )


def profile(config: Config, vault: str) -> dict[str, Any]:
    from .contracts import Policy

    with connection(config) as db:
        row = (
            db.execute("SELECT revision,value FROM profiles WHERE vault=?", (vault,)).fetchone()
            if db
            else None
        )
        return (
            {"revision": row[0], "settings": json.loads(row[1])}
            if row
            else {"revision": 0, "settings": Policy().model_dump()}
        )
