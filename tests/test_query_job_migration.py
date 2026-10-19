import sqlite3

import pytest
from schema_support import assert_graph_epoch_seed, remove_graph_epoch_schema

from fiber_bundle import database
from fiber_bundle.config import IntegrityError


def rows(db):
    return {
        r[0]: db.execute("SELECT * FROM " + r[0]).fetchall()
        for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def test_schema15_private_upgrade_preserves_all_old_rows(config, store):
    db = database.connect(config.state_root)
    db.execute("DROP TABLE document_creation_continuations")
    db.execute("DROP TABLE document_creation_deliveries")
    db.execute("DROP TABLE document_creation_saves")
    db.execute("DROP TABLE document_creation_artifacts")
    db.execute("DROP TABLE document_creation_calls")
    db.execute("DROP TABLE document_creation_sessions")
    db.execute("DROP TABLE document_creation_runs")
    db.execute("DROP TABLE query_jobs")
    remove_graph_epoch_schema(db)
    db.execute("PRAGMA user_version=15")
    before = rows(db)
    db.close()
    raw = (config.state_root / "metadata.sqlite3").read_bytes()
    with pytest.raises(IntegrityError):
        database.initialize(config)
    assert (config.state_root / "metadata.sqlite3").read_bytes() == raw
    snapshot = database.migrate(config)
    assert snapshot.stat().st_mode & 0o777 == 0o600
    db = database.connect(config.state_root)
    after = rows(db)
    assert_graph_epoch_seed(db, after)
    assert after.pop("document_creation_continuations") == []
    assert after.pop("document_creation_deliveries") == []
    assert after.pop("document_creation_saves") == []
    assert after.pop("document_creation_artifacts") == []
    assert after.pop("document_creation_calls") == []
    assert after.pop("document_creation_sessions") == []
    assert after.pop("document_creation_runs") == []
    assert after.pop("query_jobs") == [] and {
        k: [tuple(r) for r in v] for k, v in before.items()
    } == {k: [tuple(r) for r in v] for k, v in after.items()}
    assert db.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
    assert not db.execute("PRAGMA foreign_key_check").fetchall()
    db.close()


@pytest.mark.parametrize(
    "prefix",
    ["CREATE TABLE query_jobs", f"PRAGMA user_version={database.SCHEMA_VERSION}", "COMMIT"],
)
def test_additive_job_upgrade_failure_retains_schema15(config, store, monkeypatch, prefix):
    db = database.connect(config.state_root)
    db.execute("DROP TABLE document_creation_continuations")
    db.execute("DROP TABLE document_creation_deliveries")
    db.execute("DROP TABLE document_creation_saves")
    db.execute("DROP TABLE document_creation_artifacts")
    db.execute("DROP TABLE document_creation_calls")
    db.execute("DROP TABLE document_creation_sessions")
    db.execute("DROP TABLE document_creation_runs")
    db.execute("DROP TABLE query_jobs")
    remove_graph_epoch_schema(db)
    db.execute("PRAGMA user_version=15")
    db.close()

    class Fault(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.strip().startswith(prefix):
                raise sqlite3.OperationalError("synthetic job upgrade failure")
            return super().execute(sql, parameters)

    def connect(root, **kwargs):
        db = sqlite3.connect(root / "metadata.sqlite3", isolation_level=None, factory=Fault)
        db.row_factory = sqlite3.Row
        return db

    monkeypatch.setattr(database, "connect", connect)
    with pytest.raises(sqlite3.OperationalError):
        database.migrate(config)
    db = sqlite3.connect(config.state_root / "metadata.sqlite3")
    assert db.execute("PRAGMA user_version").fetchone()[0] == 15
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='query_jobs'").fetchone()
    db.close()
