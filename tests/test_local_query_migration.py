import sqlite3

import pytest
from schema_support import assert_graph_epoch_seed, remove_graph_epoch_schema
from test_execution import bound as bound
from test_execution import policy as policy

from fiber_bundle import database, execution_ledger
from fiber_bundle.config import IntegrityError

TABLES = (
    "local_query_tasks",
    "local_query_calls",
    "cloud_answer_tasks",
    "cloud_answer_selections",
    "document_creation_continuations",
    "document_creation_deliveries",
    "document_creation_saves",
    "document_creation_artifacts",
    "document_creation_calls",
    "document_creation_sessions",
    "document_creation_runs",
    "query_jobs",
)


def rows(db):
    return {
        r[0]: [tuple(v) for v in db.execute('SELECT * FROM "' + r[0] + '" ORDER BY rowid')]
        for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


@pytest.fixture
def legacy(config, bound, policy):
    db = database.connect(config.state_root)
    execution_ledger.start(config, db, bound[0], policy)
    execution_ledger.reserve(config, db, bound[0], policy)
    db.execute("DROP TABLE source_task_bindings")
    db.execute("DROP TABLE article_reviews")
    db.execute("DROP TABLE document_creation_continuations")
    db.execute("DROP TABLE document_creation_deliveries")
    db.execute("DROP TABLE document_creation_saves")
    db.execute("DROP TABLE document_creation_artifacts")
    db.execute("DROP TABLE document_creation_calls")
    db.execute("DROP TABLE document_creation_sessions")
    db.execute("DROP TABLE document_creation_runs")
    db.execute("DROP TABLE query_jobs")
    db.execute("DROP TABLE cloud_answer_selections")
    db.execute("DROP TABLE cloud_answer_tasks")
    db.execute("DROP TABLE local_query_calls")
    db.execute("DROP TABLE local_query_tasks")
    remove_graph_epoch_schema(db)
    db.execute("PRAGMA user_version=12")
    before = rows(db)
    db.close()
    assert len(before) == 29
    return before


def test_v12_upgrade_keeps_every_row_and_has_consistent_private_backup(config, legacy):
    with pytest.raises(IntegrityError, match="explicit migration"):
        database.connect(config.state_root)
    before = (config.state_root / "metadata.sqlite3").read_bytes()
    with pytest.raises(IntegrityError):
        database.initialize(config)
    assert (config.state_root / "metadata.sqlite3").read_bytes() == before
    backup = database.migrate(config)
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 12
        assert rows(old) == legacy
        assert old.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    db = database.connect(config.state_root)
    after = rows(db)
    assert_graph_epoch_seed(db, after)
    assert after.pop("source_task_bindings") == []
    assert after.pop("article_reviews") == []
    assert {k: v for k, v in after.items() if k not in TABLES} == legacy
    assert {k: after[k] for k in TABLES} == {k: [] for k in TABLES}
    assert db.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not db.execute("PRAGMA foreign_key_check").fetchall()
    db.close()
    assert database.migrate(config) is None


@pytest.mark.parametrize(
    "prefix",
    [
        "CREATE TABLE local_query_tasks",
        "CREATE TABLE local_query_calls",
        "CREATE INDEX local_query_calls_day",
        f"PRAGMA user_version={database.SCHEMA_VERSION}",
        "COMMIT",
    ],
)
def test_all_migration_faults_roll_back_and_keep_recovery_snapshot(
    config, legacy, monkeypatch, prefix
):
    class Fault(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.strip().startswith(prefix) and prefix == "COMMIT":
                raise sqlite3.OperationalError("synthetic commit fault")
            result = super().execute(sql, parameters)
            if sql.strip().startswith(prefix):
                raise sqlite3.OperationalError("synthetic schema13 fault")
            return result

    def connect(root, **kwargs):
        db = sqlite3.connect(root / "metadata.sqlite3", isolation_level=None, factory=Fault)
        db.row_factory = sqlite3.Row
        return db

    monkeypatch.setattr(database, "connect", connect)
    with pytest.raises(sqlite3.OperationalError):
        database.migrate(config)
    with sqlite3.connect(config.state_root / "metadata.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 12
        assert rows(db) == legacy
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    snapshots = list((config.state_root / "snapshots").glob("*.sqlite3"))
    assert len(snapshots) == 1
    with sqlite3.connect(snapshots[0]) as old:
        assert rows(old) == legacy


def test_local_call_rejects_nonexistent_task_foreign_key(config, store):
    db = database.connect(config.state_root)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO local_query_calls(id,task_id,config_digest,billing_day,state,created_at) "
            "VALUES('call','nonexistent','digest','2026-10-04','reserved',0)"
        )
    db.close()
