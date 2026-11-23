"""Creation-time identity rejects stale SQLite handles without changing connection modes."""

import sqlite3
from unittest.mock import patch

import pytest

from fiber_bundle import database, database_connection_identity, publication_inventory_read
from fiber_bundle.config import IntegrityError


def replacement(db, path):
    temporary = path.with_suffix(".replacement")
    with sqlite3.connect(temporary) as target:
        db.backup(target)
    temporary.replace(path)


def test_open_connection_then_replaced_database_is_rejected_before_first_reuse(config, store):
    path = config.state_root / "metadata.sqlite3"
    with database.connect(config.state_root) as db:
        replacement(db, path)
        with publication_inventory_read.reuse(db):
            with pytest.raises(IntegrityError, match="publication_location_database_changed"):
                list(publication_inventory_read.rows(db, "synthetic-vault"))


def test_replacement_while_connection_opens_is_rejected_and_handle_closed(config, store):
    path = config.state_root / "metadata.sqlite3"
    original = sqlite3.connect
    opened = []

    def connect(*args, **kwargs):
        db = original(*args, **kwargs)
        opened.append(db)
        temporary = path.with_suffix(".replacement")
        with original(temporary) as target:
            db.backup(target)
        temporary.replace(path)
        return db

    with patch.object(database.sqlite3, "connect", side_effect=connect):
        with pytest.raises(IntegrityError, match="publication_location_database_changed"):
            database.connect(config.state_root)
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")


def test_unproven_external_connection_never_enables_projection_reuse(config, store):
    with sqlite3.connect(config.state_root / "metadata.sqlite3") as db:
        db.row_factory = sqlite3.Row
        queries = []
        db.set_trace_callback(lambda sql: queries.append(sql) if " AS projected," in sql else None)
        with publication_inventory_read.reuse(db):
            list(publication_inventory_read.rows(db, "synthetic-vault"))
            list(publication_inventory_read.rows(db, "synthetic-vault"))
        assert len(queries) == 2


def test_create_readonly_migration_and_transaction_modes_remain_compatible(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    with database.connect(root, create=True) as created:
        assert isinstance(created, sqlite3.Connection)
        assert database_connection_identity.proved(created) is None
        created.execute("PRAGMA user_version=" + str(database.SCHEMA_VERSION))
        created.execute("CREATE TABLE example(value TEXT)")
        with database.transaction(created):
            created.execute("INSERT INTO example VALUES('committed')")
        with pytest.raises(RuntimeError):
            with database.transaction(created):
                created.execute("INSERT INTO example VALUES('rolled-back')")
                raise RuntimeError("synthetic rollback")
        assert created.execute("SELECT value FROM example").fetchall()[0][0] == "committed"
    with database.connect(root, readonly=True) as readonly:
        assert database_connection_identity.proved(readonly) is not None
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            readonly.execute("INSERT INTO example VALUES('denied')")
    with sqlite3.connect(root / "metadata.sqlite3") as raw:
        raw.execute("PRAGMA user_version=" + str(database.SCHEMA_VERSION - 1))
    with database.connect(root, migration=True) as migrating:
        assert migrating.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION - 1
    with pytest.raises(IntegrityError, match="explicit migration required"):
        database.connect(root)
    with pytest.raises(ValueError, match="read-only"):
        database.connect(root, readonly=True, create=True)
    with pytest.raises(ValueError, match="read-only"):
        database.connect(root, readonly=True, migration=True)
