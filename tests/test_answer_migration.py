import sqlite3
from unittest.mock import patch

import pytest

from fiber_bundle import answer_migration, auth, database


def rows(db):
    tables = [
        r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    ]
    columns = {t: [r[1] for r in db.execute(f'PRAGMA table_info("{t}")')] for t in tables}
    values = {
        t: [tuple(r) for r in db.execute(f'SELECT * FROM "{t}" ORDER BY rowid')] for t in tables
    }
    return columns, values


@pytest.fixture
def legacy(config):
    config.state_root.mkdir()
    db = sqlite3.connect(config.state_root / "metadata.sqlite3", isolation_level=None)
    db.row_factory = sqlite3.Row
    db.executescript(
        database.SCHEMA
        + database.SCHEMA_V2
        + database.SCHEMA_V3
        + database.SCHEMA_V4
        + database.SCHEMA_V5
        + database.SCHEMA_V6
    )
    db.execute("PRAGMA user_version=6")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute(
        "INSERT INTO vaults(id,name,relative_path) "
        "VALUES ('vault','personal','test vault/temp_vault1')"
    )
    client = auth.create_client(db, "synthetic migration candidate", False)
    vid = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    # Synthetic rows exercise every incoming execution/answer/publication/cost reference.
    db.execute(
        "INSERT INTO sources VALUES (?,?,?,?)",
        ("source", vid, "synthetic", "2026-09-28"),
    )
    db.execute(
        "INSERT INTO source_versions VALUES (?,?,?,?,?,?)",
        ("version", "source", 1, "synthetic-digest", "session", "2026-09-28"),
    )
    db.execute(
        "INSERT INTO receipts(job_id,ingress_scope,event_id,input_digest,"
        "manifest_json,state,created_at) "
        "VALUES ('receipt','synthetic','event','digest','{}','receiving','2026-09-28')"
    )
    for rid in ("read", "article", "answer"):
        db.execute(
            "INSERT INTO execution_runs VALUES (?,?,?,?,?,'queued',0,?,'USD',1,1,NULL)",
            (rid, client["id"], vid, "version", "synthetic-config", 1000),
        )
    db.execute("INSERT INTO answer_tasks VALUES ('answer','receipt','{}','[]',NULL)")
    db.execute(
        "INSERT INTO article_tasks(run_id,receipt_id,source_url,worker_image,worker_timeout,"
        "capture_task_id,state) VALUES ('article','receipt','https://example.com','synthetic',1,'capture','queued')"
    )
    db.execute(
        "INSERT INTO article_publications VALUES ('publication',?,'https://example.com','digest',1,1,"
        "'version','synthetic/note','{}','preparing',NULL,NULL)",
        (vid,),
    )
    db.execute("INSERT INTO article_publication_runs VALUES ('article','publication','receipt')")
    for index, state in enumerate(("reserved", "settled", "uncertain")):
        db.execute(
            "INSERT INTO model_calls VALUES (?,?,'model','test','2026-09-28','USD',100,?,?,?, ?,1)",
            (
                str(index),
                ("read", "article", "answer")[index],
                50 if state == "settled" else None,
                1 if state == "settled" else None,
                1 if state == "settled" else None,
                state,
            ),
        )
    before = rows(db)
    db.close()
    return before


def test_nonempty_v6_preserves_all_rows_cost_states_and_foreign_keys(config, legacy):
    backup = database.migrate(config)
    assert backup is not None
    with sqlite3.connect(backup) as old:
        assert rows(old) == legacy
        assert old.execute("PRAGMA user_version").fetchone()[0] == 6
    db = database.connect(config.state_root)
    columns, before = legacy
    assert len(columns) == 17
    for table, names in columns.items():
        selected = ",".join('"' + n + '"' for n in names)
        assert [
            tuple(r) for r in db.execute(f'SELECT {selected} FROM "{table}" ORDER BY rowid')
        ] == before[table]
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert {r[0] for r in db.execute("SELECT binding_kind FROM execution_runs")} == {"source"}
    assert {r[0] for r in db.execute("SELECT input_kind FROM answer_tasks")} == {"receipt"}
    for sql in [
        "UPDATE execution_runs SET version_id=NULL WHERE id='read'",
        "UPDATE answer_tasks SET receipt_id=NULL WHERE run_id='answer'",
        "DELETE FROM execution_runs WHERE id='article'",
    ]:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(sql)
    db.close()
    assert database.migrate(config) is None


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO execution_runs_new",
        "DROP TABLE execution_runs",
        "INSERT INTO answer_tasks_new",
        "DROP TABLE answer_tasks",
        f"PRAGMA user_version={database.SCHEMA_VERSION}",
    ],
)
def test_transaction_failures_restore_entire_legacy_database(config, legacy, statement):
    original_connect = database.connect

    class FailingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            result = super().execute(sql, parameters)
            if sql.startswith(statement):
                raise sqlite3.OperationalError("synthetic migration fault")
            return result

    def fault_connect(root, **kwargs):
        db = sqlite3.connect(
            root / "metadata.sqlite3", isolation_level=None, factory=FailingConnection
        )
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    with patch.object(database, "connect", side_effect=fault_connect):
        with pytest.raises(sqlite3.OperationalError, match="synthetic migration fault"):
            database.migrate(config)
    db = original_connect(config.state_root, migration=True)
    assert rows(db) == legacy
    assert db.execute("PRAGMA user_version").fetchone()[0] == 6
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    db.close()
    assert len(list((config.state_root / "snapshots").glob("*.sqlite3"))) == 1


def test_backup_failure_leaves_legacy_unchanged(config, legacy):
    with patch.object(database, "backup_database", side_effect=OSError("synthetic backup failure")):
        with pytest.raises(OSError):
            database.migrate(config)
    with sqlite3.connect(config.state_root / "metadata.sqlite3") as db:
        assert rows(db) == legacy
        assert db.execute("PRAGMA user_version").fetchone()[0] == 6


def test_foreign_key_failure_rolls_back(config, legacy):
    upgrade = answer_migration.upgrade

    def corrupt(db):
        upgrade(db)
        db.execute("UPDATE execution_runs SET client_id='absent' WHERE id='read'")

    with patch.object(answer_migration, "upgrade", side_effect=corrupt):
        with pytest.raises(database.IntegrityError, match="foreign key"):
            database.migrate(config)
    with sqlite3.connect(config.state_root / "metadata.sqlite3") as db:
        assert rows(db) == legacy
        assert db.execute("PRAGMA user_version").fetchone()[0] == 6
