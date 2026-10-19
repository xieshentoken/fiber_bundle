import sqlite3

import pytest
from schema_support import assert_graph_epoch_seed, remove_graph_epoch_schema
from test_document_creation_execution import prepared_task as prepared_task
from test_document_creation_tasks import input_task as input_task
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner

from fiber_bundle import database, document_creation_tasks
from fiber_bundle.config import IntegrityError


def rows(db):
    return {
        r[0]: [tuple(v) for v in db.execute('SELECT * FROM "' + r[0] + '" ORDER BY rowid')]
        for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


@pytest.fixture
def legacy(config, prepared_task):
    db = database.connect(config.state_root)
    db.execute("DROP TABLE document_creation_continuations")
    db.execute("DROP TABLE document_creation_deliveries")
    db.execute("DROP TABLE document_creation_saves")
    db.execute("DROP TABLE document_creation_artifacts")
    db.execute("DROP TABLE document_creation_calls")
    db.execute("DROP TABLE document_creation_sessions")
    db.execute("DROP TABLE document_creation_runs")
    remove_graph_epoch_schema(db)
    db.execute("PRAGMA user_version=16")
    before = rows(db)
    db.close()
    return before, prepared_task, (config.state_root / "document-creation.sqlite3").read_bytes()


def test_explicit16_upgrade_adds_empty_binding_table_and_keeps_main_and_sidecar(
    config, owner, legacy
):
    old_rows, task, sidecar = legacy
    before = (config.state_root / "metadata.sqlite3").read_bytes()
    with pytest.raises(IntegrityError):
        database.initialize(config)
    assert (config.state_root / "metadata.sqlite3").read_bytes() == before
    snapshot = database.migrate(config)
    assert snapshot.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(snapshot) as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 16
        assert rows(old) == old_rows
    db = database.connect(config.state_root)
    after = rows(db)
    assert_graph_epoch_seed(db, after)
    assert after.pop("document_creation_continuations") == []
    assert after.pop("document_creation_deliveries") == []
    assert after.pop("document_creation_saves") == []
    assert after.pop("document_creation_artifacts") == []
    assert after.pop("document_creation_calls") == []
    assert after.pop("document_creation_sessions") == []
    assert after.pop("document_creation_runs") == [] and after == old_rows
    assert db.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not db.execute("PRAGMA foreign_key_check").fetchall()
    with database.locked(config):
        assert document_creation_tasks.status(config, db, owner[0], task["task_id"]) == task
    db.close()
    assert (config.state_root / "document-creation.sqlite3").read_bytes() == sidecar
    assert database.migrate(config) is None


@pytest.mark.parametrize(
    "prefix",
    [
        "CREATE TABLE document_creation_runs",
        f"PRAGMA user_version={database.SCHEMA_VERSION}",
        "COMMIT",
    ],
)
def test_upgrade_fault_restores_schema16_and_retains_snapshot(config, legacy, monkeypatch, prefix):
    class Fault(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.strip().startswith(prefix) and prefix == "COMMIT":
                raise sqlite3.OperationalError("synthetic creation migration fault")
            result = super().execute(sql, parameters)
            if sql.strip().startswith(prefix):
                raise sqlite3.OperationalError("synthetic creation migration fault")
            return result

    def connect(root, **kwargs):
        db = sqlite3.connect(root / "metadata.sqlite3", isolation_level=None, factory=Fault)
        db.row_factory = sqlite3.Row
        return db

    monkeypatch.setattr(database, "connect", connect)
    with pytest.raises(sqlite3.OperationalError):
        database.migrate(config)
    with sqlite3.connect(config.state_root / "metadata.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 16
        assert rows(db) == legacy[0]
    snapshots = list((config.state_root / "snapshots").glob("*.sqlite3"))
    assert len(snapshots) == 1
    with sqlite3.connect(snapshots[0]) as old:
        assert rows(old) == legacy[0]
    assert (config.state_root / "document-creation.sqlite3").read_bytes() == legacy[2]
