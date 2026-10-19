import sqlite3

import pytest
from schema_support import assert_graph_epoch_seed, remove_graph_epoch_schema
from test_document_creation_config import creation_policy as creation_policy
from test_document_creation_execution import claim
from test_document_creation_execution import prepared_task as prepared_task
from test_document_creation_migration import rows
from test_document_creation_tasks import input_task as input_task
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner

from fiber_bundle import database
from fiber_bundle import document_creation_execution as execution
from fiber_bundle.config import IntegrityError
from fiber_bundle.document_creation_config import parse

TABLES = (
    "document_creation_continuations",
    "document_creation_deliveries",
    "document_creation_saves",
    "document_creation_artifacts",
    "document_creation_calls",
    "document_creation_sessions",
)


@pytest.fixture
def legacy17(config, owner, prepared_task, creation_policy):
    with execution.responsibility(config, owner[0], prepared_task["task_id"]) as guard:
        with database.locked(config):
            db = database.connect(config.state_root)
            claim(config, db, owner[0], prepared_task, parse(creation_policy), guard)
            db.close()
    db = database.connect(config.state_root)
    for name in TABLES:
        db.execute("DROP TABLE " + name)
    remove_graph_epoch_schema(db)
    db.execute("PRAGMA user_version=17")
    before = rows(db)
    assert len(before["document_creation_runs"]) == 1 and len(before["model_calls"]) == 1
    db.close()
    return before, (config.state_root / "document-creation.sqlite3").read_bytes()


def test_explicit17_upgrade_preserves_fixed_owner_unknown_fee_and_sidecar(config, legacy17):
    before, sidecar = legacy17
    raw = (config.state_root / "metadata.sqlite3").read_bytes()
    with pytest.raises(IntegrityError):
        database.initialize(config)
    assert (config.state_root / "metadata.sqlite3").read_bytes() == raw
    backup = database.migrate(config)
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 17 and rows(db) == before
    db = database.connect(config.state_root)
    current = rows(db)
    assert_graph_epoch_seed(db, current)
    for name in TABLES:
        assert current.pop(name) == []
    assert current == before
    assert db.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not db.execute("PRAGMA foreign_key_check").fetchall()
    db.close()
    assert (config.state_root / "document-creation.sqlite3").read_bytes() == sidecar
    assert database.migrate(config) is None


@pytest.mark.parametrize(
    "prefix",
    [
        *("CREATE TABLE " + t for t in TABLES),
        f"PRAGMA user_version={database.SCHEMA_VERSION}",
        "COMMIT",
    ],
)
def test17_upgrade_failure_rolls_back_all_rows_and_keeps_private_snapshot(
    config, legacy17, monkeypatch, prefix
):
    class Fault(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.strip().startswith(prefix) and prefix == "COMMIT":
                raise sqlite3.OperationalError("synthetic runtime migration fault")
            result = super().execute(sql, parameters)
            if sql.strip().startswith(prefix):
                raise sqlite3.OperationalError("synthetic runtime migration fault")
            return result

    def connect(root, **kwargs):
        db = sqlite3.connect(root / "metadata.sqlite3", isolation_level=None, factory=Fault)
        db.row_factory = sqlite3.Row
        return db

    monkeypatch.setattr(database, "connect", connect)
    with pytest.raises(sqlite3.OperationalError):
        database.migrate(config)
    with sqlite3.connect(config.state_root / "metadata.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 17 and rows(db) == legacy17[0]
    snapshots = list((config.state_root / "snapshots").glob("*.sqlite3"))
    assert len(snapshots) == 1 and snapshots[0].stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(snapshots[0]) as db:
        assert rows(db) == legacy17[0]
    assert (config.state_root / "document-creation.sqlite3").read_bytes() == legacy17[1]
