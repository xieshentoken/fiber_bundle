import sqlite3

import pytest
from schema_support import assert_graph_epoch_seed, assert_preserved_rows, remove_graph_epoch_schema
from test_media_jobs import create
from test_media_jobs import media_scope as media_scope

from fiber_bundle import database

TABLES = (
    "media_cleanup",
    "media_link_tasks",
    "media_publications",
    "media_reviews",
    "media_processing",
)


def legacy(config, scope):
    job = create(config, scope)
    db = database.connect(config.state_root)
    db.execute(
        "UPDATE media_tasks SET state='failed',error_code='retained_failure' WHERE id=?", (job,)
    )
    for table in (
        "document_creation_continuations",
        "document_creation_deliveries",
        "document_creation_saves",
        "document_creation_artifacts",
        "document_creation_calls",
        "document_creation_sessions",
        "document_creation_runs",
        "query_jobs",
        "cloud_answer_selections",
        "cloud_answer_tasks",
        "local_query_calls",
        "local_query_tasks",
    ) + TABLES:
        db.execute("DROP TABLE " + table)
    remove_graph_epoch_schema(db)
    db.execute("PRAGMA user_version=11")
    before = {
        r[0]: db.execute('SELECT * FROM "' + r[0] + '" ORDER BY rowid').fetchall()
        for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    before = {k: [tuple(r) for r in rows] for k, rows in before.items()}
    db.close()
    return before


def test_schema11_upgrade_is_additive_and_backup_is_consistent(config, media_scope):
    before = legacy(config, media_scope)
    backup = database.migrate(config)
    with sqlite3.connect(backup) as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 11
        for t, rows in before.items():
            assert old.execute('SELECT * FROM "' + t + '" ORDER BY rowid').fetchall() == rows
    db = database.connect(config.state_root)
    assert_graph_epoch_seed(db)
    for t, rows in before.items():
        assert_preserved_rows(db, t, rows)
    assert all(db.execute("SELECT count(*) FROM " + t).fetchone()[0] == 0 for t in TABLES)
    assert (
        db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        and not db.execute("PRAGMA foreign_key_check").fetchall()
    )
    db.close()
    assert database.migrate(config) is None


@pytest.mark.parametrize(
    "prefix",
    tuple("CREATE TABLE " + t for t in TABLES)
    + (f"PRAGMA user_version={database.SCHEMA_VERSION}",),
)
def test_every_schema12_fault_rolls_back_without_changing_old_rows(
    config, media_scope, monkeypatch, prefix
):
    before = legacy(config, media_scope)

    class Fault(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            result = super().execute(sql, parameters)
            if sql.strip().startswith(prefix):
                raise sqlite3.OperationalError("synthetic schema12 fault")
            return result

    def connect(root, **kw):
        db = sqlite3.connect(root / "metadata.sqlite3", isolation_level=None, factory=Fault)
        db.row_factory = sqlite3.Row
        return db

    monkeypatch.setattr(database, "connect", connect)
    with pytest.raises(sqlite3.OperationalError):
        database.migrate(config)
    with sqlite3.connect(config.state_root / "metadata.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 11
        for t, rows in before.items():
            assert db.execute('SELECT * FROM "' + t + '" ORDER BY rowid').fetchall() == rows
        assert all(
            not db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone()
            for t in TABLES
        )
