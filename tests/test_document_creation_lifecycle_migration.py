"""Explicit schema 18 migration preserves completed private draft and transcripts."""

import sqlite3

import pytest
from schema_support import assert_graph_epoch_seed, remove_graph_epoch_schema
from test_document_creation_lifecycle import attached as attached
from test_document_creation_lifecycle import completed as completed
from test_document_creation_lifecycle import creation_policy as creation_policy
from test_document_creation_lifecycle import input_task as input_task
from test_document_creation_lifecycle import owner as owner
from test_document_creation_lifecycle import prepared_task as prepared_task
from test_document_creation_lifecycle import routing as routing
from test_document_creation_migration import rows

from fiber_bundle import database

NEW = ("document_creation_continuations", "document_creation_deliveries", "document_creation_saves")


@pytest.fixture
def legacy18(config, completed):
    db, _, attempt, raw, metadata = completed
    for name in NEW:
        db.execute("DROP TABLE " + name)
    # Reconstruct the actual old constraint, including all four stored columns.
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute(
        "CREATE TABLE old_sessions (run_id TEXT PRIMARY KEY "
        "REFERENCES document_creation_runs(run_id), profile_json TEXT NOT NULL "
        "CHECK(json_valid(profile_json)), profile_sha256 TEXT NOT NULL, "
        "mode TEXT NOT NULL CHECK(mode='synthetic')) STRICT"
    )
    db.execute("INSERT INTO old_sessions SELECT * FROM document_creation_sessions")
    db.execute("DROP TABLE document_creation_sessions")
    db.execute("ALTER TABLE old_sessions RENAME TO document_creation_sessions")
    remove_graph_epoch_schema(db)
    db.execute("PRAGMA user_version=18")
    before = rows(db)
    assert len(before["document_creation_artifacts"]) == 1
    assert len(before["document_creation_calls"]) == 2
    return before, (config.state_root / "document-creation.sqlite3").read_bytes()


def test18_explicit_upgrade_preserves_full_old_rows_and_private_backup(config, legacy18):
    backup = database.migrate(config)
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 18
        assert rows(old) == legacy18[0]
    db = database.connect(config.state_root)
    now = rows(db)
    assert_graph_epoch_seed(db, now)
    for table in NEW:
        assert now.pop(table) == []
    assert now == legacy18[0]
    assert not db.execute("PRAGMA foreign_key_check").fetchall()
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
    db.close()
    assert (config.state_root / "document-creation.sqlite3").read_bytes() == legacy18[1]


@pytest.mark.parametrize(
    "prefix",
    [
        "INSERT INTO document_creation_sessions_v19",
        "DROP TABLE document_creation_sessions",
        "ALTER TABLE document_creation_sessions_v19",
        *("CREATE TABLE " + n for n in NEW),
        f"PRAGMA user_version={database.SCHEMA_VERSION}",
        "COMMIT",
    ],
)
def test18_faults_roll_back_transcripts_artifact_and_session_constraint(
    config, legacy18, monkeypatch, prefix
):
    class Fault(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.strip().startswith(prefix) and prefix == "COMMIT":
                raise sqlite3.OperationalError("synthetic migration fault")
            result = super().execute(sql, parameters)
            if sql.strip().startswith(prefix):
                raise sqlite3.OperationalError("synthetic migration fault")
            return result

    def connect(root, **kwargs):
        db = sqlite3.connect(root / "metadata.sqlite3", isolation_level=None, factory=Fault)
        db.row_factory = sqlite3.Row
        return db

    monkeypatch.setattr(database, "connect", connect)
    with pytest.raises(sqlite3.OperationalError):
        database.migrate(config)
    with sqlite3.connect(config.state_root / "metadata.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 18
        assert rows(db) == legacy18[0]
        assert (
            "mode='synthetic'"
            in db.execute(
                "SELECT sql FROM sqlite_master WHERE name='document_creation_sessions'"
            ).fetchone()[0]
        )
    assert (config.state_root / "document-creation.sqlite3").read_bytes() == legacy18[1]
    monkeypatch.undo()
    database.migrate(config)
