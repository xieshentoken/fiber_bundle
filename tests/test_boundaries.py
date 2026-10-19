import json
import os
import sqlite3
from dataclasses import replace

import pytest

from fiber_bundle import database, objects
from fiber_bundle.config import IntegrityError, InvalidInput, load_config


@pytest.mark.parametrize(
    "relative",
    ["../outside", "/tmp/outside", "", ".", "test vault", "test vault/temp_vault1/state"],
)
def test_reject_bad_state_root(config, relative):
    with pytest.raises(InvalidInput):
        database.initialize(replace(config, state_relative=relative))
    assert not config.state_root.exists()


def test_config_symlink_and_wrong_types_rejected(config):
    root = config.project_root
    link = root / "link.json"
    link.symlink_to(root / "config.json")
    with pytest.raises(InvalidInput):
        load_config(link, root)
    (root / "config.json").write_text(json.dumps({"state_root": 42, "vaults": {}}))
    with pytest.raises(InvalidInput):
        load_config(root / "config.json", root)


def test_dangling_and_parent_symlinks_rejected(config, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    config.state_root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(InvalidInput):
        database.initialize(config)
    assert not list(outside.iterdir())
    (config.project_root / config.state_relative).unlink()
    (config.project_root / "test vault").symlink_to(tmp_path / "missing")
    with pytest.raises(InvalidInput):
        database.initialize(config)
    assert not config.state_root.exists()


def test_object_directory_symlink_rejected(store, config, request_data, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (config.state_root / "objects").rmdir()
    (config.state_root / "objects").symlink_to(outside, target_is_directory=True)
    with pytest.raises(InvalidInput):
        store.ingest(**request_data)
    assert not list(outside.iterdir())


def test_initialize_repeat_and_unknown_schema_unchanged(store, config):
    database.initialize(config)
    connection = database.connect(config.state_root)
    assert connection.execute("SELECT count(*) FROM vaults").fetchone()[0] == 2
    connection.execute("PRAGMA user_version=9000")
    connection.close()
    path = config.state_root / "metadata.sqlite3"
    before = path.read_bytes()
    with pytest.raises(IntegrityError):
        database.initialize(config)
    with pytest.raises(IntegrityError):
        store.recover()
    assert path.read_bytes() == before


def test_explicit_empty_database_upgrade_backs_up_before_schema(config):
    config.state_root.mkdir()
    connection = sqlite3.connect(config.state_root / "metadata.sqlite3")
    connection.execute("PRAGMA user_version=0")
    connection.close()
    database.initialize(config)
    snapshots = list((config.state_root / "snapshots").glob("*.sqlite3"))
    assert len(snapshots) == 1
    backup = sqlite3.connect(snapshots[0])
    try:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 0
        assert backup.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
    finally:
        backup.close()


def test_populated_unversioned_database_not_overwritten(config):
    config.state_root.mkdir()
    connection = sqlite3.connect(config.state_root / "metadata.sqlite3")
    connection.execute("CREATE TABLE unrelated(data TEXT)")
    connection.close()
    before = (config.state_root / "metadata.sqlite3").read_bytes()
    with pytest.raises(IntegrityError):
        database.initialize(config)
    assert (config.state_root / "metadata.sqlite3").read_bytes() == before


def test_fifo_is_rejected_without_blocking(config, store, request_data):
    connection = database.connect(config.state_root)
    vault_id = connection.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    connection.close()
    raw = request_data["raw_event"]
    relative = objects.object_relative(vault_id, objects.digest(raw))
    path = config.state_root / relative
    path.parent.mkdir(parents=True)
    os.mkfifo(path)
    with pytest.raises(IntegrityError):
        store.ingest(**request_data)


def test_empty_bytes_are_valid_but_declared_missing_is_not_success(store, request_data):
    from fiber_bundle.inputs import AssetInput

    result = store.ingest(
        **{
            **request_data,
            "raw_event": b"",
            "items": (AssetInput(b"", "empty.bin", "application/octet-stream"),),
        }
    )
    assert result.state == "stored"
