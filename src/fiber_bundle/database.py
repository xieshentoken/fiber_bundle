"""SQLite schema, explicit initialization, locking and consistent snapshots."""

import fcntl
import math
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from uuid import uuid4

from fiber_bundle import (
    answer_migration,
    document_capacity_schema,
    document_profile_schema,
    expert_schema,
    publication_layout_schema,
    source_import_schema,
    system_schema,
    vault_catalog_schema,
    vault_save_schema,
)
from fiber_bundle.answer_schema import SCHEMA_V6
from fiber_bundle.article_schema import SCHEMA_V4
from fiber_bundle.cloud_answer_schema import SCHEMA_V14
from fiber_bundle.cloud_answer_selection_schema import SCHEMA_V15
from fiber_bundle.config import (
    Config,
    IntegrityError,
    contained,
    make_directory,
    sync_directory,
)
from fiber_bundle.document_creation_schema import SCHEMA_V17, SCHEMA_V18, SCHEMA_V19
from fiber_bundle.document_schema import SCHEMA_V8
from fiber_bundle.execution_schema import SCHEMA_V3
from fiber_bundle.graph_permission_schema import SCHEMA_V21
from fiber_bundle.local_query_schema import SCHEMA_V13
from fiber_bundle.material_schema import SCHEMA_V9
from fiber_bundle.media_glossary_schema import SCHEMA_V11
from fiber_bundle.media_processing_schema import SCHEMA_V12
from fiber_bundle.media_schema import SCHEMA_V10
from fiber_bundle.publication_schema import SCHEMA_V5
from fiber_bundle.query_job_schema import SCHEMA_V16
from fiber_bundle.source_schema import SCHEMA_V20

SCHEMA_VERSION = 35
_wait_limit: ContextVar[float | None] = ContextVar("storage_wait_limit", default=None)
_held: ContextVar[frozenset[tuple[int, str]]] = ContextVar("storage_held", default=frozenset())


class LockTimeout(TimeoutError):
    """No storage lease was acquired; this is distinct from an external IO timeout."""


@contextmanager
def lock_wait(seconds: float) -> Iterator[None]:
    """Bound every synchronous lock wait in this operation, including worker threads."""
    if not math.isfinite(seconds) or not 0 < seconds <= 60:
        raise ValueError("invalid storage lock wait")
    token = _wait_limit.set(seconds)
    try:
        yield
    finally:
        _wait_limit.reset(token)


SCHEMA = """
CREATE TABLE vaults (
 id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, relative_path TEXT NOT NULL UNIQUE
) STRICT;
CREATE TABLE sources (
 id TEXT PRIMARY KEY, vault_id TEXT NOT NULL REFERENCES vaults(id),
 source_key TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(vault_id, source_key)
) STRICT;
CREATE TABLE source_versions (
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
 version_no INTEGER NOT NULL CHECK(version_no > 0), manifest_digest TEXT NOT NULL,
 retention TEXT NOT NULL CHECK(retention IN ('session','knowledge')), created_at TEXT NOT NULL,
 UNIQUE(source_id, version_no)
) STRICT;
CREATE TABLE assets (
 id TEXT PRIMARY KEY, vault_id TEXT NOT NULL REFERENCES vaults(id), sha256 TEXT NOT NULL,
 byte_count INTEGER NOT NULL CHECK(byte_count >= 0), relative_path TEXT NOT NULL UNIQUE,
 UNIQUE(vault_id, sha256)
) STRICT;
CREATE TABLE receipts (
 job_id TEXT PRIMARY KEY, ingress_scope TEXT NOT NULL, event_id TEXT NOT NULL,
 input_digest TEXT NOT NULL, manifest_json TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('receiving','stored','incomplete','failed')),
 source_version_id TEXT REFERENCES source_versions(id), raw_asset_id TEXT REFERENCES assets(id),
 error_code TEXT, created_at TEXT NOT NULL,
 CHECK(state != 'stored' OR (source_version_id IS NOT NULL AND raw_asset_id IS NOT NULL)),
 UNIQUE(ingress_scope, event_id)
) STRICT;
CREATE TABLE version_assets (
 version_id TEXT NOT NULL REFERENCES source_versions(id),
 asset_id TEXT NOT NULL REFERENCES assets(id), ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
 role TEXT NOT NULL, original_name TEXT NOT NULL, media_type TEXT NOT NULL,
 PRIMARY KEY(version_id, ordinal)
) STRICT;
CREATE TABLE session_refs (
 session_scope TEXT NOT NULL, session_id TEXT NOT NULL,
 version_id TEXT NOT NULL REFERENCES source_versions(id),
 PRIMARY KEY(session_scope, session_id, version_id)
) STRICT;
"""


@contextmanager
def locked(config: Config) -> Iterator[None]:
    config.validate()
    path = contained(config.state_root, "storage.lock")
    key = (threading.get_ident(), str(path))
    if key in _held.get():
        raise RuntimeError("nested storage lock acquisition")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        wait = _wait_limit.get()
        if wait is None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        else:
            deadline = time.monotonic() + wait
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise LockTimeout("storage lock wait expired") from None
                    time.sleep(min(0.02, max(0, deadline - time.monotonic())))
        _held.set(_held.get() | {key})
        try:
            yield
        finally:
            # HTTP owns the lease across ASGI contexts (enter in a worker, exit on
            # the loop); ContextVar tokens cannot be reset in a different context.
            _held.set(_held.get() - {key})
    finally:
        os.close(fd)


def connect(
    root: Path, *, create: bool = False, migration: bool = False, readonly: bool = False
) -> sqlite3.Connection:
    from .restore_guard import permit

    permit(root)
    path = contained(root, "metadata.sqlite3")
    for suffix in ("-journal", "-wal", "-shm"):
        contained(root, "metadata.sqlite3" + suffix)
    if readonly and (create or migration):
        raise ValueError("read-only connections cannot create or migrate")
    connection = sqlite3.connect(
        path.as_uri() + ("?mode=ro" if readonly else "?mode=rwc" if create else "?mode=rw"),
        uri=True,
        isolation_level=None,
        timeout=min(5, _wait_limit.get() or 5),
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=EXTRA")
        connection.execute("PRAGMA fullfsync=ON")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if (
            version != SCHEMA_VERSION
            and not (create and version == 0)
            and not (migration and version in range(1, SCHEMA_VERSION))
        ):
            raise IntegrityError("unsupported database schema; explicit migration required")
    except BaseException:
        connection.close()
        raise
    return connection


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[None]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def backup_database(connection: sqlite3.Connection, root: Path) -> Path:
    directory = contained(root, "snapshots")
    make_directory(directory)
    path = directory / f"{uuid4()}.sqlite3"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    target = sqlite3.connect(path)
    try:
        connection.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise IntegrityError("database snapshot integrity check failed")
    finally:
        target.close()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    sync_directory(directory)
    return path


def initialize(config: Config) -> None:
    config.validate()
    root = config.state_root
    make_directory(root)
    with locked(config):
        for relative in ("objects", "staging"):
            make_directory(contained(root, relative))
        existing = contained(root, "metadata.sqlite3").exists()
        connection = connect(root, create=True)
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                tables = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
                if tables:
                    raise IntegrityError("unversioned populated database cannot be migrated")
                if existing:
                    backup_database(connection, root)
                connection.execute("PRAGMA foreign_keys=OFF")
                with transaction(connection):
                    for statement in (
                        SCHEMA
                        + SCHEMA_V2
                        + SCHEMA_V3
                        + SCHEMA_V4
                        + SCHEMA_V5
                        + SCHEMA_V6
                        + SCHEMA_V8
                        + SCHEMA_V9
                        + SCHEMA_V10
                        + SCHEMA_V11
                        + SCHEMA_V12
                        + SCHEMA_V13
                        + SCHEMA_V14
                        + SCHEMA_V15
                        + SCHEMA_V16
                        + SCHEMA_V17
                        + SCHEMA_V18
                        + SCHEMA_V19
                        + SCHEMA_V20
                    ).split(";"):
                        if statement.strip():
                            connection.execute(statement)
                    for statement in SCHEMA_V21:
                        connection.execute(statement)
                    answer_migration.upgrade(connection)
                    source_import_schema.upgrade(connection)
                    document_capacity_schema.upgrade(connection)
                    document_profile_schema.upgrade(connection)
                    expert_schema.upgrade(connection)
                    vault_catalog_schema.upgrade(connection)
                    from .agent_connections.schema import upgrade as agent_upgrade

                    agent_upgrade(connection)
                    system_schema.upgrade(connection)
                    vault_save_schema.upgrade(connection)
                    publication_layout_schema.upgrade(connection)
                    from .automatic_schema import upgrade as automatic_upgrade

                    automatic_upgrade(connection)
                    from .model_budget_schema import upgrade as budget_upgrade

                    budget_upgrade(connection)
                    from .video_schema import upgrade as video_upgrade

                    video_upgrade(connection)
                    from .model_configuration.schema import upgrade as models_upgrade

                    models_upgrade(connection)
                    from .model_configuration.ledger_schema import upgrade as ledger_upgrade

                    ledger_upgrade(connection)
                    if connection.execute("PRAGMA foreign_key_check").fetchall():
                        raise IntegrityError("initialization foreign key check failed")
                    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                connection.execute("PRAGMA foreign_keys=ON")
            with transaction(connection):
                for name, relative in config.vaults:
                    row = connection.execute(
                        "SELECT relative_path FROM vaults WHERE name=?", (name,)
                    ).fetchone()
                    if row is not None and row[0] != relative:
                        raise IntegrityError("vault path differs from initialized database")
                    connection.execute(
                        "INSERT OR IGNORE INTO vaults(id,name,relative_path) VALUES (?,?,?)",
                        (str(uuid4()), name, relative),
                    )
            for _, relative in config.vaults:
                make_directory(config.vault_path(relative))
            os.chmod(contained(root, "metadata.sqlite3"), 0o600)
            sync_directory(root)
        finally:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.close()


# Kept separate so migration tests can construct the exact M1 schema.
SCHEMA_V2 = """
CREATE TABLE clients (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, token_digest TEXT NOT NULL UNIQUE,
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 create_vault INTEGER NOT NULL CHECK(create_vault IN (0,1)),
 created_at REAL NOT NULL, revoked_at REAL
) STRICT;
CREATE TABLE client_vault_grants (
 client_id TEXT NOT NULL REFERENCES clients(id), vault_id TEXT NOT NULL REFERENCES vaults(id),
 can_query INTEGER NOT NULL CHECK(can_query IN (0,1)),
 can_original INTEGER NOT NULL CHECK(can_original IN (0,1)),
 can_add INTEGER NOT NULL CHECK(can_add IN (0,1)), PRIMARY KEY(client_id,vault_id)
) STRICT;
CREATE TABLE admin_auth (
 id INTEGER PRIMARY KEY CHECK(id=1), salt BLOB NOT NULL, password_hash BLOB NOT NULL,
 n INTEGER NOT NULL, r INTEGER NOT NULL, p INTEGER NOT NULL, version INTEGER NOT NULL
) STRICT;
CREATE TABLE admin_sessions (
 token_digest TEXT PRIMARY KEY, csrf_digest TEXT NOT NULL,
 expires_at REAL NOT NULL, credential_version INTEGER NOT NULL
) STRICT;
ALTER TABLE vaults ADD COLUMN status TEXT NOT NULL DEFAULT 'active'
 CHECK(status IN ('pending','active'));
ALTER TABLE vaults ADD COLUMN default_query INTEGER NOT NULL DEFAULT 0
 CHECK(default_query IN (0,1));
ALTER TABLE vaults ADD COLUMN requested_by_client_id TEXT REFERENCES clients(id);
ALTER TABLE vaults ADD COLUMN directory_dev INTEGER;
ALTER TABLE vaults ADD COLUMN directory_ino INTEGER;
ALTER TABLE receipts ADD COLUMN owner_client_id TEXT REFERENCES clients(id);
"""


def migrate(config: Config) -> Path | None:
    """Explicit upgrade; durable backup precedes the schema transaction."""
    with locked(config):
        connection = connect(config.state_root, migration=True)
        try:
            if connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION:
                return None
            backup = backup_database(connection, config.state_root)
            connection.execute("PRAGMA foreign_keys=OFF")
            with transaction(connection):
                previous = connection.execute("PRAGMA user_version").fetchone()[0]
                changes = (
                    (SCHEMA_V2 if previous == 1 else "")
                    + (SCHEMA_V3 if previous < 3 else "")
                    + (SCHEMA_V4 if previous < 4 else "")
                    + (SCHEMA_V5 if previous < 5 else "")
                    + (SCHEMA_V6 if previous < 6 else "")
                )
                for statement in changes.split(";"):
                    if statement.strip():
                        connection.execute(statement)
                if previous < 7:
                    answer_migration.upgrade(connection)
                for statement in (
                    (SCHEMA_V8 if previous < 8 else "")
                    + (SCHEMA_V9 if previous < 9 else "")
                    + (SCHEMA_V10 if previous < 10 else "")
                    + (SCHEMA_V11 if previous < 11 else "")
                    + (SCHEMA_V12 if previous < 12 else "")
                    + (SCHEMA_V13 if previous < 13 else "")
                    + (SCHEMA_V14 if previous < 14 else "")
                    + (SCHEMA_V15 if previous < 15 else "")
                    + (SCHEMA_V16 if previous < 16 else "")
                    + (SCHEMA_V17 if previous < 17 else "")
                    + (SCHEMA_V18 if previous < 18 else "")
                    + (SCHEMA_V19 if previous < 19 else "")
                    + (SCHEMA_V20 if previous < 20 else "")
                ).split(";"):
                    if statement.strip():
                        connection.execute(statement)
                if previous < 21:
                    for statement in SCHEMA_V21:
                        connection.execute(statement)
                if previous < 22:
                    source_import_schema.upgrade(connection)
                if previous < 23:
                    document_capacity_schema.upgrade(connection)
                if previous < 24:
                    document_profile_schema.upgrade(connection)
                if previous < 25:
                    expert_schema.upgrade(connection)
                if previous < 26:
                    vault_catalog_schema.upgrade(connection)
                if previous < 27:
                    from .agent_connections.schema import upgrade as agent_upgrade

                    agent_upgrade(connection)
                if previous < 28:
                    system_schema.upgrade(connection)
                if previous < 29:
                    vault_save_schema.upgrade(connection)
                if previous < 30:
                    publication_layout_schema.upgrade(connection)
                if previous < 31:
                    from .automatic_schema import upgrade as automatic_upgrade

                    automatic_upgrade(connection)
                if previous < 32:
                    from .model_budget_schema import upgrade as budget_upgrade

                    budget_upgrade(connection)
                if previous < 33:
                    from .video_schema import upgrade as video_upgrade

                    video_upgrade(connection)
                if previous < 34:
                    from .model_configuration.schema import upgrade as models_upgrade

                    models_upgrade(connection)
                if previous < 35:
                    from .model_configuration.ledger_schema import upgrade as ledger_upgrade

                    ledger_upgrade(connection)
                if connection.execute("PRAGMA foreign_key_check").fetchall():
                    raise IntegrityError("migration foreign key check failed")
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            return backup
        finally:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.close()
