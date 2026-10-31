"""Durable event cursor, coalesced work and metadata catalogue, outside the Vault."""

import json
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from ..config import Config, IntegrityError, InvalidInput, contained, sync_directory
from ..inputs import canonical
from ..vault_scope import Scope, prefix

NAME = "vault-sync.sqlite3"
QUIET = 5.0
AUDIT_INTERVAL = 86400.0
DELAYS = (60, 300, 900, 1800)
SCHEMA = """
CREATE TABLE libraries (
 id TEXT PRIMARY KEY, binding TEXT NOT NULL, root TEXT NOT NULL, scope TEXT NOT NULL,
 cursor TEXT NOT NULL DEFAULT '0', generation INTEGER NOT NULL DEFAULT 0,
 indexed INTEGER NOT NULL DEFAULT -1, baseline INTEGER NOT NULL DEFAULT 0,
 last_audit REAL NOT NULL DEFAULT 0, heartbeat REAL NOT NULL DEFAULT 0,
 state TEXT NOT NULL DEFAULT 'pending', error TEXT, checked_at REAL NOT NULL DEFAULT 0,
 scans INTEGER NOT NULL DEFAULT 0, stats INTEGER NOT NULL DEFAULT 0,
 bodies INTEGER NOT NULL DEFAULT 0, retries INTEGER NOT NULL DEFAULT 0,
 index_after REAL NOT NULL DEFAULT 0, index_attempts INTEGER NOT NULL DEFAULT 0
) STRICT;
CREATE TABLE pending (
 vault TEXT NOT NULL, path TEXT NOT NULL, flags INTEGER NOT NULL,
 revision INTEGER NOT NULL, due REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(vault,path)
) STRICT;
CREATE TABLE files (
 vault TEXT NOT NULL, path TEXT NOT NULL, signature TEXT NOT NULL, size INTEGER NOT NULL,
 directory INTEGER NOT NULL, revision INTEGER NOT NULL, PRIMARY KEY(vault,path)
) STRICT;
CREATE TABLE issues (
 vault TEXT NOT NULL, path TEXT NOT NULL, reason TEXT NOT NULL, revision INTEGER NOT NULL,
 next_check REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(vault,path)
) STRICT;
CREATE TABLE working_versions (
 vault TEXT NOT NULL, path TEXT NOT NULL, revision INTEGER NOT NULL,
 sha256 TEXT NOT NULL, value TEXT NOT NULL, state TEXT NOT NULL,
 PRIMARY KEY(vault,path,revision)
) STRICT;
CREATE TABLE directory_scans (
 vault TEXT NOT NULL, path TEXT NOT NULL, signature TEXT NOT NULL, names TEXT NOT NULL,
 position INTEGER NOT NULL, PRIMARY KEY(vault,path)
) STRICT;
CREATE TABLE revisions (
 vault TEXT NOT NULL, path TEXT NOT NULL, revision INTEGER NOT NULL,
 old_signature TEXT, new_signature TEXT, flags INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(vault,path,revision)
) STRICT;
"""


@contextmanager
def connection(config: Config, *, write: bool = False) -> Iterator[sqlite3.Connection | None]:
    path = contained(config.state_root, NAME)
    if not path.exists() and not write:
        yield None
        return
    new = False
    if write and not path.exists():
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            sync_directory(path.parent)
            new = True
        except FileExistsError:
            pass
    if path.is_symlink() or path.stat().st_nlink != 1 or path.stat().st_size > 256 * 1024**2:
        raise IntegrityError("vault_sync_ledger_invalid")
    for suffix in ("-journal", "-wal", "-shm"):
        contained(config.state_root, NAME + suffix)
    db = sqlite3.connect(path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True)
    db.row_factory = sqlite3.Row
    try:
        if write:
            db.execute("PRAGMA synchronous=EXTRA")
            db.execute("PRAGMA fullfsync=ON")
            db.execute("BEGIN IMMEDIATE")
        if new:
            for sql in SCHEMA.split(";"):
                if sql.strip():
                    db.execute(sql)
            db.execute("PRAGMA user_version=1")
        elif db.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise IntegrityError("vault_sync_ledger_version")
        yield db
        if write:
            db.commit()
    finally:
        db.close()


def library(config: Config, identity: str) -> dict[str, Any] | None:
    with connection(config) as db:
        row = (
            db.execute("SELECT * FROM libraries WHERE id=?", (identity,)).fetchone() if db else None
        )
        return dict(row) if row else None


def register(config: Config, identity: str, root: str, scope: Scope, binding: str) -> None:
    with connection(config, write=True) as db:
        assert db is not None
        old = db.execute("SELECT * FROM libraries WHERE id=?", (identity,)).fetchone()
        if old and old["binding"] == binding:
            return
        # A changed root/scope cannot inherit the old cursor or make stale data authoritative.
        db.execute("DELETE FROM pending WHERE vault=?", (identity,))
        db.execute("DELETE FROM files WHERE vault=?", (identity,))
        db.execute("DELETE FROM directory_scans WHERE vault=?", (identity,))
        db.execute("DELETE FROM libraries WHERE id=?", (identity,))
        db.execute(
            "INSERT INTO libraries(id,root,scope,binding) VALUES(?,?,?,?)",
            (identity, root, canonical(scope.wire()), binding),
        )
        db.execute("INSERT INTO pending VALUES(?,?,?,?,?,0)", (identity, "", 1, 0, 0.0))


def scope_of(row: dict[str, Any]) -> Scope:
    value = json.loads(row["scope"])
    return Scope(tuple(value["include"]), tuple(value["exclude"]), value["policy_sha256"])


def accepted(scope: Scope, path: str) -> bool:
    try:
        if prefix(path) != path or not scope.reachable(path):
            return False
    except (ValueError, InvalidInput):
        return False
    markers = {
        ".fiber-publication.json",
        ".fiber-analysis.json",
        ".knowledge-organizer-policy.json",
    }
    return not any(p.startswith(".") and p not in markers for p in path.split("/"))


def enqueue(
    config: Config,
    identity: str,
    events: list[tuple[str, int]],
    cursor: str,
    *,
    now: float | None = None,
) -> None:
    now = time.time() if now is None else now
    if not cursor.isdecimal() or len(cursor) > 20 or len(events) > 4096:
        raise IntegrityError("vault_sync_event_invalid")
    with connection(config, write=True) as db:
        assert db is not None
        raw = db.execute("SELECT * FROM libraries WHERE id=?", (identity,)).fetchone()
        if raw is None:
            raise IntegrityError("vault_sync_unknown_library")
        row = dict(raw)
        scope = scope_of(row)
        allowed = [(p, f) for p, f in events if accepted(scope, p)]
        generation = row["generation"] + bool(allowed)
        for path, flags in allowed:
            # A new notification extends the quiet window but never resets a failure cooldown.
            db.execute(
                "INSERT INTO pending VALUES(?,?,?,?,?,0) ON CONFLICT(vault,path) DO UPDATE SET "
                "flags=flags|excluded.flags,revision=excluded.revision,"
                "due=MAX(pending.due,excluded.due)",
                (identity, path, flags, generation, now + QUIET),
            )
        if (
            db.execute("SELECT count(*) FROM pending WHERE vault=?", (identity,)).fetchone()[0]
            > 10000
        ):
            # Event overload becomes one bounded reconciliation, not a lost change.
            due = db.execute("SELECT MAX(due) FROM pending WHERE vault=?", (identity,)).fetchone()[
                0
            ]
            db.execute("DELETE FROM pending WHERE vault=?", (identity,))
            db.execute(
                "INSERT INTO pending VALUES(?,?,?,?,?,0)", (identity, "", 1, generation, due)
            )
        db.execute(
            "UPDATE libraries SET cursor=?,generation=?,heartbeat=?,state=? WHERE id=?",
            (cursor, generation, now, "pending" if allowed else row["state"], identity),
        )


def due(config: Config, *, now: float | None = None, limit: int = 100) -> list[dict[str, Any]]:
    with connection(config) as db:
        return (
            [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM pending WHERE due<=? ORDER BY due,vault,path LIMIT ?",
                    (time.time() if now is None else now, limit),
                )
            ]
            if db
            else []
        )


def failed(config: Config, task: dict[str, Any], code: str, *, now: float | None = None) -> None:
    now = time.time() if now is None else now
    delay = DELAYS[min(task["attempts"], len(DELAYS) - 1)]
    with connection(config, write=True) as db:
        assert db is not None
        db.execute(
            "UPDATE pending SET due=MAX(due,?),attempts=attempts+1 WHERE vault=? AND path=?",
            (now + delay, task["vault"], task["path"]),
        )
        db.execute(
            "UPDATE libraries SET state='waiting',error=?,retries=retries+1 WHERE id=?",
            (code, task["vault"]),
        )
        db.execute(
            "UPDATE libraries SET baseline=1 WHERE id=? AND EXISTS "
            "(SELECT 1 FROM files WHERE vault=? AND path='') AND NOT EXISTS "
            "(SELECT 1 FROM pending WHERE vault=? AND attempts=0)",
            (task["vault"], task["vault"], task["vault"]),
        )


def status(config: Config, identity: str) -> dict[str, Any] | None:
    row = library(config, identity)
    if row is None:
        return None
    with connection(config) as db:
        assert db is not None
        count, next_at, active = db.execute(
            "SELECT count(*),min(due),COALESCE(sum(attempts=0),0) FROM pending WHERE vault=?",
            (identity,),
        ).fetchone()
        issues = db.execute("SELECT count(*) FROM issues WHERE vault=?", (identity,)).fetchone()[0]
    return {
        k: row[k]
        for k in (
            "state",
            "generation",
            "indexed",
            "baseline",
            "checked_at",
            "error",
            "last_audit",
            "scans",
            "stats",
            "bodies",
            "retries",
            "heartbeat",
            "index_after",
            "index_attempts",
        )
    } | {
        "pending": count,
        "active_pending": active,
        "next_check": next_at,
        "needs_attention": issues,
    }
