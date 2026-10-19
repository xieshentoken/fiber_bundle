import sqlite3

import pytest
from schema_support import assert_graph_epoch_seed, assert_preserved_rows, remove_graph_epoch_schema
from test_document_tasks import create
from test_document_tasks import document_scope as document_scope
from test_execution import policy as policy

from fiber_bundle import database, document_bindings
from fiber_bundle.config import IntegrityError


def legacy(config, policy, scope):
    rid = create(config, policy, scope)
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
        "document_publications",
        "document_reviews",
        "document_profiles",
    ):
        db.execute("DROP TABLE " + table)
    remove_graph_epoch_schema(db)
    db.execute("PRAGMA user_version=8")
    before = {
        r[0]: [tuple(row) for row in db.execute("SELECT * FROM " + r[0] + " ORDER BY rowid")]
        for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    db.close()
    return rid, before


def test_v8_additive_review_migration_preserves_all_rows_and_old_pdf_bindings(
    config, policy, document_scope
):
    rid, before = legacy(config, policy, document_scope)
    with pytest.raises(IntegrityError):
        database.connect(config.state_root)
    backup = database.migrate(config)
    assert backup
    old = sqlite3.connect(backup)
    assert old.execute("PRAGMA user_version").fetchone()[0] == 8
    old.close()
    db = database.connect(config.state_root)
    assert_graph_epoch_seed(db)
    for table, rows in before.items():
        assert_preserved_rows(db, table, rows)
    bound = document_bindings.get(db, rid)
    assert (
        bound["format"] == "pdf" and bound["profile"] == "native" and bound["runtime_json"] is None
    )
    assert (
        db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        and not db.execute("PRAGMA foreign_key_check").fetchall()
    )
    db.close()
    assert database.migrate(config) is None


@pytest.mark.parametrize(
    "prefix",
    [
        "CREATE TABLE document_profiles",
        "CREATE TABLE document_reviews",
        "CREATE TABLE document_publications",
        f"PRAGMA user_version={database.SCHEMA_VERSION}",
    ],
)
def test_v8_failed_upgrade_preserves_all_rows_and_schema(
    config, policy, document_scope, monkeypatch, prefix
):
    _, before = legacy(config, policy, document_scope)

    class Fault(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            value = super().execute(sql, parameters)
            if sql.strip().startswith(prefix):
                raise sqlite3.OperationalError("synthetic review migration fault")
            return value

    def connection(root, **kw):
        db = sqlite3.connect(root / "metadata.sqlite3", isolation_level=None, factory=Fault)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    monkeypatch.setattr(database, "connect", connection)
    with pytest.raises(sqlite3.OperationalError):
        database.migrate(config)
    db = sqlite3.connect(config.state_root / "metadata.sqlite3")
    assert db.execute("PRAGMA user_version").fetchone()[0] == 8
    for table, rows in before.items():
        assert db.execute("SELECT * FROM " + table + " ORDER BY rowid").fetchall() == rows
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='document_reviews'").fetchone()
    db.close()
