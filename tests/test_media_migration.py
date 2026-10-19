import sqlite3

import pytest
from schema_support import assert_graph_epoch_seed, assert_preserved_rows, remove_graph_epoch_schema

from fiber_bundle import database
from fiber_bundle.config import IntegrityError


def legacy(config, store):
    db = database.connect(config.state_root)
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
        "media_cleanup",
        "media_link_tasks",
        "media_publications",
        "media_reviews",
        "media_processing",
        "media_glossary_confirmations",
        "media_glossary_revisions",
        "media_tasks",
    ):
        db.execute("DROP TABLE " + table)
    remove_graph_epoch_schema(db)
    db.execute("PRAGMA user_version=9")
    before = {
        r[0]: [tuple(row) for row in db.execute('SELECT * FROM "' + r[0] + '" ORDER BY rowid')]
        for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    db.close()
    return before


def test_additive_v9_snapshot_and_all_existing_rows_preserved(config, store):
    before = legacy(config, store)
    with pytest.raises(IntegrityError):
        database.connect(config.state_root)
    backup = database.migrate(config)
    assert backup is not None
    with sqlite3.connect(backup) as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 9
    db = database.connect(config.state_root)
    for table, rows in before.items():
        assert_preserved_rows(db, table, rows)
    assert db.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
    assert db.execute("SELECT count(*) FROM media_tasks").fetchone()[0] == 0
    assert_graph_epoch_seed(db)
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not db.execute("PRAGMA foreign_key_check").fetchall()
    db.close()
    assert database.migrate(config) is None


@pytest.mark.parametrize(
    "prefix", ["CREATE TABLE media_tasks", f"PRAGMA user_version={database.SCHEMA_VERSION}"]
)
def test_failed_v9_upgrade_rolls_back_and_keeps_snapshot(config, store, monkeypatch, prefix):
    before = legacy(config, store)

    class Fault(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            value = super().execute(sql, parameters)
            if sql.strip().startswith(prefix):
                raise sqlite3.OperationalError("synthetic fault")
            return value

    def connection(root, **kw):
        db = sqlite3.connect(root / "metadata.sqlite3", isolation_level=None, factory=Fault)
        db.row_factory = sqlite3.Row
        return db

    monkeypatch.setattr(database, "connect", connection)
    with pytest.raises(sqlite3.OperationalError):
        database.migrate(config)
    with sqlite3.connect(config.state_root / "metadata.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 9
        for table, rows in before.items():
            assert db.execute('SELECT * FROM "' + table + '" ORDER BY rowid').fetchall() == rows
