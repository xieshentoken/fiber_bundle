import sqlite3

import pytest
from schema_support import assert_graph_epoch_seed, remove_graph_epoch_schema
from test_cloud_answer_migration import rows
from test_cloud_answer_tasks import cloud_task as cloud_task
from test_local_query_tasks import local_task as local_task
from test_local_query_tasks import route as route
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner

from fiber_bundle import database
from fiber_bundle.config import IntegrityError


@pytest.fixture
def legacy(config, cloud_task):
    db = database.connect(config.state_root)
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
    remove_graph_epoch_schema(db)
    db.execute("PRAGMA user_version=14")
    original = rows(db)
    db.close()
    assert len(original) == 32
    return original


def test_schema14_upgrade_preserves_pending_cloud_intents_and_all_old_rows(config, legacy):
    before = (config.state_root / "metadata.sqlite3").read_bytes()
    with pytest.raises(IntegrityError):
        database.initialize(config)
    assert (config.state_root / "metadata.sqlite3").read_bytes() == before
    backup = database.migrate(config)
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 14 and rows(db) == legacy
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
    assert after.pop("query_jobs") == []
    assert after.pop("cloud_answer_selections") == [] and after == legacy
    assert db.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not db.execute("PRAGMA foreign_key_check").fetchall()
    db.close()
    assert database.migrate(config) is None


@pytest.mark.parametrize(
    "prefix",
    [
        "CREATE TABLE cloud_answer_selections",
        "CREATE UNIQUE INDEX cloud_answer_selected_once",
        f"PRAGMA user_version={database.SCHEMA_VERSION}",
        "COMMIT",
    ],
)
def test_selection_schema_failures_roll_back_and_keep_private_snapshot(
    config, legacy, monkeypatch, prefix
):
    class Fault(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.strip().startswith(prefix) and prefix == "COMMIT":
                raise sqlite3.OperationalError("synthetic commit failure")
            result = super().execute(sql, parameters)
            if sql.strip().startswith(prefix):
                raise sqlite3.OperationalError("synthetic selection schema failure")
            return result

    def connect(root, **kwargs):
        db = sqlite3.connect(root / "metadata.sqlite3", isolation_level=None, factory=Fault)
        db.row_factory = sqlite3.Row
        return db

    monkeypatch.setattr(database, "connect", connect)
    with pytest.raises(sqlite3.OperationalError):
        database.migrate(config)
    with sqlite3.connect(config.state_root / "metadata.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 14 and rows(db) == legacy
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    snapshots = list((config.state_root / "snapshots").glob("*.sqlite3"))
    assert len(snapshots) == 1
    with sqlite3.connect(snapshots[0]) as db:
        assert rows(db) == legacy
