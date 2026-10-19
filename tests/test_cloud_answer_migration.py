import sqlite3

import pytest
from schema_support import assert_graph_epoch_seed, remove_graph_epoch_schema
from test_local_query_tasks import body
from test_local_query_tasks import local_task as local_task
from test_local_query_tasks import route as route
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner

from fiber_bundle import database, local_query_ledger
from fiber_bundle.config import IntegrityError


def rows(db):
    return {
        r[0]: [tuple(v) for v in db.execute('SELECT * FROM "' + r[0] + '" ORDER BY rowid')]
        for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


@pytest.fixture
def legacy(config, local_task, route, owner):
    db = database.connect(config.state_root)
    owned = local_query_ledger.claim(config, db, route, owner[0], local_task[0])
    local_query_ledger.finish(config, db, route, owned, body(route, owned))
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
    remove_graph_epoch_schema(db)
    db.execute("PRAGMA user_version=13")
    before = rows(db)
    db.close()
    assert len(before) == 31
    return before


def test_schema13_upgrade_keeps_local_result_and_every_old_row(config, legacy):
    before = (config.state_root / "metadata.sqlite3").read_bytes()
    with pytest.raises(IntegrityError):
        database.initialize(config)
    assert (config.state_root / "metadata.sqlite3").read_bytes() == before
    backup = database.migrate(config)
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 13
        assert rows(old) == legacy
    db = database.connect(config.state_root)
    after = rows(db)
    assert_graph_epoch_seed(db, after)
    assert after.pop("source_task_bindings") == []
    assert after.pop("article_reviews") == []
    assert after.pop("document_creation_continuations") == []
    assert after.pop("document_creation_deliveries") == []
    assert after.pop("document_creation_saves") == []
    assert after.pop("document_creation_artifacts") == []
    assert after.pop("document_creation_calls") == []
    assert after.pop("document_creation_sessions") == []
    assert after.pop("document_creation_runs") == []
    assert after.pop("cloud_answer_tasks") == []
    assert after.pop("query_jobs") == []
    assert after.pop("cloud_answer_selections") == [] and after == legacy
    assert db.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not db.execute("PRAGMA foreign_key_check").fetchall()
    db.close()
    assert database.migrate(config) is None


@pytest.mark.parametrize(
    "prefix",
    ["CREATE TABLE cloud_answer_tasks", f"PRAGMA user_version={database.SCHEMA_VERSION}", "COMMIT"],
)
def test_every_additive_upgrade_fault_rolls_back_and_preserves_backup(
    config, legacy, monkeypatch, prefix
):
    class Fault(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.strip().startswith(prefix) and prefix == "COMMIT":
                raise sqlite3.OperationalError("synthetic commit failure")
            result = super().execute(sql, parameters)
            if sql.strip().startswith(prefix):
                raise sqlite3.OperationalError("synthetic schema14 failure")
            return result

    def connect(root, **kwargs):
        db = sqlite3.connect(root / "metadata.sqlite3", isolation_level=None, factory=Fault)
        db.row_factory = sqlite3.Row
        return db

    monkeypatch.setattr(database, "connect", connect)
    with pytest.raises(sqlite3.OperationalError):
        database.migrate(config)
    with sqlite3.connect(config.state_root / "metadata.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 13
        assert rows(db) == legacy
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    snapshots = list((config.state_root / "snapshots").glob("*.sqlite3"))
    assert len(snapshots) == 1
    with sqlite3.connect(snapshots[0]) as old:
        assert rows(old) == legacy
