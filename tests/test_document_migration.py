import sqlite3

import pytest
from schema_support import assert_graph_epoch_seed, assert_preserved_rows, remove_graph_epoch_schema

from fiber_bundle import auth, database
from fiber_bundle.config import IntegrityError


@pytest.fixture
def legacy_v7(config, store):
    db = database.connect(config.state_root)
    client = auth.create_client(db, "migration-owner", False)
    vault = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    db.execute(
        "INSERT INTO execution_runs(id,client_id,vault_id,version_id,config_digest,state,"
        "task_limit_nusd,currency,created_at,updated_at,binding_kind) "
        "VALUES('notes',?,?,NULL,'synthetic','queued',1,'USD',1,1,'notes')",
        (client["id"], vault),
    )
    db.execute(
        "INSERT INTO answer_tasks(run_id,receipt_id,request_json,snapshot_json,input_kind) "
        "VALUES('notes',NULL,'{}','{}','notes')"
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
        "document_tasks",
    ):
        db.execute(f"DROP TABLE {table}")
    remove_graph_epoch_schema(db)
    db.execute("PRAGMA user_version=7")
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    before = {
        table: [tuple(r) for r in db.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
        for table in tables
    }
    db.close()
    return before


def test_additive_v7_migration_preserves_all_rows_including_note_bindings(config, legacy_v7):
    with pytest.raises(IntegrityError, match="explicit migration"):
        database.connect(config.state_root)
    backup = database.migrate(config)
    assert backup is not None
    old = sqlite3.connect(backup)
    assert old.execute("PRAGMA user_version").fetchone()[0] == 7
    old.close()
    db = database.connect(config.state_root)
    assert db.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
    for table, rows in legacy_v7.items():
        assert_preserved_rows(db, table, rows)
    assert db.execute("SELECT count(*) FROM document_tasks").fetchone()[0] == 0
    assert_graph_epoch_seed(db)
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not db.execute("PRAGMA foreign_key_check").fetchall()
    db.close()
    assert database.migrate(config) is None


@pytest.mark.parametrize(
    "prefix", ["CREATE TABLE document_tasks", f"PRAGMA user_version={database.SCHEMA_VERSION}"]
)
def test_failed_migration_rolls_back_new_table_and_version(config, legacy_v7, monkeypatch, prefix):
    class Fault(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            result = super().execute(sql, parameters)
            if sql.strip().startswith(prefix):
                raise sqlite3.OperationalError("synthetic migration fault")
            return result

    def connect(root, **kwargs):
        db = sqlite3.connect(root / "metadata.sqlite3", isolation_level=None, factory=Fault)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    monkeypatch.setattr(database, "connect", connect)
    with pytest.raises(sqlite3.OperationalError):
        database.migrate(config)
    db = sqlite3.connect(config.state_root / "metadata.sqlite3")
    assert db.execute("PRAGMA user_version").fetchone()[0] == 7
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='document_tasks'").fetchone()
    for table, rows in legacy_v7.items():
        assert db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall() == rows
    assert not db.execute("PRAGMA foreign_key_check").fetchall()
    db.close()
