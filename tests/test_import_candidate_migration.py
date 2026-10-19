"""True pre-capacity/conversion/full-compiler candidates, backup and transactional rollback."""

import sqlite3

import pytest
from schema_support import remove_agent_schema
from test_session_search import owner as session_owner  # noqa: F401
from test_source_imports import owner as owner
from test_source_imports import request, upload

from fiber_bundle import database
from fiber_bundle.document_schema import SCHEMA_V8
from fiber_bundle.source_schema import SCHEMA_V20


def old_candidate(config, version):
    db = database.connect(config.state_root)
    db.execute("PRAGMA foreign_keys=OFF")
    for table in ("source_expert_publications", "source_expert_attempts", "source_expert_tasks"):
        db.execute("DROP TABLE " + table)
    if version < 24:
        db.executescript(SCHEMA_V20.split("CREATE TABLE IF NOT EXISTS source_task_bindings")[0])
    if version < 23:
        rows = db.execute("SELECT * FROM document_tasks").fetchall()
        db.execute("DROP TABLE document_tasks")
        db.executescript(SCHEMA_V8)
        if rows:
            db.executemany(
                "INSERT INTO document_tasks VALUES(" + ",".join("?" for _ in rows[0]) + ")", rows
            )
    remove_agent_schema(db)
    for column in ("absolute_root", "root_kind", "read_only", "attachment_state", "scope_json"):
        db.execute("ALTER TABLE vaults DROP COLUMN " + column)
    db.execute("DROP TABLE vault_admissions")
    db.execute("DROP TABLE vault_operations")
    db.execute(f"PRAGMA user_version={version}")
    before = {
        row[0]: [tuple(r) for r in db.execute('SELECT * FROM "' + row[0] + '"')]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    db.close()
    return before


@pytest.mark.parametrize("version", [22, 23, 24])
def test_real_candidate_upgrade_and_snapshot_preserve_nonempty_rows(config, store, owner, version):
    cid, _, ids = owner
    db = database.connect(config.state_root)
    item = upload(
        config,
        db,
        request(cid, ids["personal"], b"controlled legacy original"),
        b"controlled legacy original",
    )
    db.close()
    before = old_candidate(config, version)
    backup = database.migrate(config)
    assert backup is not None
    old = sqlite3.connect(backup)
    assert old.execute("PRAGMA user_version").fetchone()[0] == version
    old.close()
    db = database.connect(config.state_root)
    for name, rows in before.items():
        assert [
            tuple(r)[:-5] if name == "vaults" else tuple(r)
            for r in db.execute('SELECT * FROM "' + name + '"')
        ] == rows
    assert db.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
    assert not db.execute("PRAGMA foreign_key_check").fetchall()
    assert store.read_asset(item["asset_id"], vault="personal") == b"controlled legacy original"
    db.close()


def test_new_table_fault_rolls_back_whole_candidate(config, store, owner, monkeypatch):
    before = old_candidate(config, 23)

    class Fault(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if "CREATE TABLE source_expert_attempts" in sql:
                raise sqlite3.OperationalError("synthetic candidate migration fault")
            return super().execute(sql, *args, **kwargs)

    def connect(root, **kwargs):
        db = sqlite3.connect(root / "metadata.sqlite3", isolation_level=None, factory=Fault)
        db.row_factory = sqlite3.Row
        return db

    monkeypatch.setattr(database, "connect", connect)
    with pytest.raises(sqlite3.OperationalError, match="synthetic candidate"):
        database.migrate(config)
    db = sqlite3.connect(config.state_root / "metadata.sqlite3")
    assert db.execute("PRAGMA user_version").fetchone()[0] == 23
    after = {
        row[0]: db.execute('SELECT * FROM "' + row[0] + '"').fetchall()
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert after == before
    assert not db.execute("PRAGMA foreign_key_check").fetchall()
    db.close()
