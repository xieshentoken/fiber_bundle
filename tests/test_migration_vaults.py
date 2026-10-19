import sqlite3
from unittest.mock import patch

import pytest
from schema_support import assert_graph_epoch_seed
from test_api import add_client, payload

from fiber_bundle import database
from fiber_bundle.config import IntegrityError
from fiber_bundle.inputs import AssetInput
from fiber_bundle.store import Store


def create_v1(config):
    config.state_root.mkdir()
    db = sqlite3.connect(config.state_root / "metadata.sqlite3")
    db.executescript(database.SCHEMA)
    db.execute("PRAGMA user_version=1")
    db.commit()
    db.close()


def test_explicit_migration_backup_repeat_and_old_rejection(config):
    create_v1(config)
    before = (config.state_root / "metadata.sqlite3").read_bytes()
    with pytest.raises(IntegrityError):
        database.initialize(config)
    assert before == (config.state_root / "metadata.sqlite3").read_bytes()
    backup = database.migrate(config)
    assert backup is not None
    db = sqlite3.connect(backup)
    assert db.execute("PRAGMA user_version").fetchone()[0] == 1
    db.close()
    assert database.migrate(config) is None
    database.initialize(config)
    db = database.connect(config.state_root)
    assert len(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()) == 67
    assert_graph_epoch_seed(db)
    assert db.execute("SELECT count(*) FROM clients").fetchone()[0] == 0
    db.close()


def test_migration_failure_rolls_back_and_retains_backup(config):
    create_v1(config)
    with patch.object(database, "SCHEMA_V2", database.SCHEMA_V2 + "INVALID SQL;"):
        with pytest.raises(sqlite3.Error):
            database.migrate(config)
    db = sqlite3.connect(config.state_root / "metadata.sqlite3")
    assert db.execute("PRAGMA user_version").fetchone()[0] == 1
    assert len(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()) == 7
    db.close()
    assert len(list((config.state_root / "snapshots").glob("*.sqlite3"))) == 1
    assert database.migrate(config)


def test_v1_metadata_migrates_without_modifying_originals(config):
    # Self-contained M1-format fixture, so the source distribution can run tests
    # without depending on an evidence directory that is intentionally not packaged.
    database.initialize(config)
    store = Store(config)
    for index, name in enumerate(("personal", "supplier")):
        store.ingest(
            ingress_scope="synthetic",
            event_id=str(index),
            vault=name,
            source_key="source",
            raw_event=b"event",
            items=(
                AssetInput(b"body", "body.txt", "text/plain"),
                AssetInput(b"attachment", "sample.bin", "application/octet-stream"),
            ),
            session_scope="synthetic",
            session_id="session",
        )
    original = database.connect(config.state_root)
    old_path = config.state_root / "v1-fixture.sqlite3"
    target = sqlite3.connect(old_path)
    target.executescript(database.SCHEMA)
    for table in (
        "vaults",
        "sources",
        "source_versions",
        "assets",
        "receipts",
        "version_assets",
        "session_refs",
    ):
        columns = ",".join(row[1] for row in target.execute("PRAGMA table_info(" + table + ")"))
        rows = original.execute("SELECT " + columns + " FROM " + table).fetchall()
        for row in rows:
            target.execute(
                "INSERT INTO " + table + " VALUES(" + ",".join("?" for _ in row) + ")", tuple(row)
            )
    target.execute("PRAGMA user_version=1")
    target.commit()
    before = {
        table: target.execute("SELECT * FROM " + table).fetchall()
        for table in ("sources", "source_versions", "assets", "version_assets", "session_refs")
    }
    original.close()
    target.close()
    old_path.replace(config.state_root / "metadata.sqlite3")
    database.migrate(config)
    db = database.connect(config.state_root)
    for table, rows in before.items():
        assert [tuple(row) for row in db.execute("SELECT * FROM " + table)] == rows
    assert (
        db.execute("SELECT count(*) FROM receipts WHERE owner_client_id IS NULL").fetchone()[0] == 2
    )
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    db.close()


def request_vault(http, name="synthetic-new-vault"):
    cid, headers = add_client(http, create=True)
    response = http.post("/v1/vaults", headers=headers, json={"name": name})
    assert response.status_code == 201
    return cid, headers, response.json()["id"]


def approve(http, cid, vid):
    return http.post(
        "/admin/vaults/" + vid + "/approve",
        json={"client_id": cid, "query": False, "original": False, "add": True},
    )


def test_pending_approval_and_storage_recovery(http, config):
    cid, headers, vid = request_vault(http)
    assert http.get("/v1/vaults", headers=headers).json() == []
    assert http.post("/v1/receipts", headers=headers, json=payload(vid)).status_code == 404
    assert (
        http.put(
            "/admin/clients/" + cid + "/vaults/" + vid,
            json={"query": True, "original": True, "add": True},
        ).status_code
        == 422
    )
    path = config.project_root / "test vault" / ("created-" + vid)
    assert path.is_dir() and list(path.iterdir()) == []
    assert approve(http, cid, vid).status_code == 200
    saved = http.post("/v1/receipts", headers=headers, json=payload(vid))
    assert saved.status_code == 202 and saved.json()["receipt_state"] == "stored"
    assert http.get("/v1/vaults/" + vid + "/versions", headers=headers).status_code == 404
    assert Store(config).recover()[0].state == "stored"
    assert Store(config).snapshot().exists()
    assert list(path.iterdir()) == []


@pytest.mark.parametrize("replacement", ["symlink", "empty-replacement", "nonempty"])
def test_approval_refuses_directory_changes(http, config, replacement):
    cid, headers, vid = request_vault(http)
    path = config.project_root / "test vault" / ("created-" + vid)
    if replacement == "symlink":
        path.rmdir()
        path.symlink_to(config.project_root / "test vault/temp_vault1", target_is_directory=True)
    elif replacement == "empty-replacement":
        path.rename(path.with_name(path.name + "-preserved"))
        path.mkdir()
    else:
        (path / "not-empty.txt").write_text("synthetic")
    try:
        assert approve(http, cid, vid).status_code in {422, 503}
        assert http.post("/v1/receipts", headers=headers, json=payload(vid)).status_code == 404
    finally:
        if path.is_symlink():
            path.unlink()


def test_active_directory_replacement_blocks_intake(http, config):
    cid, headers, vid = request_vault(http)
    assert approve(http, cid, vid).status_code == 200
    path = config.project_root / "test vault" / ("created-" + vid)
    path.rename(path.with_name(path.name + "-preserved"))
    path.mkdir()
    assert http.post("/v1/receipts", headers=headers, json=payload(vid)).status_code == 503


def test_pending_missing_directory_can_resume_but_existing_untracked_is_not_adopted(http, config):
    from fiber_bundle import vaults

    original = vaults._create_directory

    def failure(*args):
        raise OSError("synthetic interruption before mkdir")

    with patch.object(vaults, "_create_directory", failure):
        cid, headers = add_client(http, create=True)
        assert (
            http.post("/v1/vaults", headers=headers, json={"name": "interrupted"}).status_code
            == 503
        )
    vid = next(v["id"] for v in http.get("/admin/vaults").json() if v["name"] == "interrupted")
    assert approve(http, cid, vid).status_code == 200

    def orphan(config, connection, row):
        (config.project_root / row["relative_path"]).mkdir()
        raise OSError("synthetic interruption after mkdir")

    with patch.object(vaults, "_create_directory", orphan):
        assert http.post("/v1/vaults", headers=headers, json={"name": "orphan"}).status_code == 503
    vid = next(v["id"] for v in http.get("/admin/vaults").json() if v["name"] == "orphan")
    assert approve(http, cid, vid).status_code == 503
    assert original is vaults._create_directory


def test_invalid_approval_rolls_back_activation(http):
    cid, headers, vid = request_vault(http)
    assert approve(http, "unknown-client", vid).status_code == 404
    assert http.post("/v1/receipts", headers=headers, json=payload(vid)).status_code == 404
    assert approve(http, cid, vid).status_code == 200
    assert approve(http, cid, vid).status_code == 422


def test_new_vault_rejects_caller_paths_and_root_symlink(http, config):
    cid, headers = add_client(http, create=True)
    assert (
        http.post(
            "/v1/vaults", headers=headers, json={"name": "outside", "path": "/tmp/outside"}
        ).status_code
        == 422
    )
    root = config.project_root / "test vault"
    backup = config.project_root / "test-vault-preserved"
    root.rename(backup)
    root.symlink_to(backup, target_is_directory=True)
    try:
        assert http.post("/v1/vaults", headers=headers, json={"name": "outside"}).status_code == 503
    finally:
        root.unlink()
        backup.rename(root)
