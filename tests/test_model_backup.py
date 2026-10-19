"""Complete encrypted backups rebind synthetic managed secrets without resetting budgets."""

import json
import sqlite3
from pathlib import Path

import pytest

from fiber_bundle import backup_bundle, backup_restore, database, model_budgets
from fiber_bundle.config import Config, IntegrityError
from fiber_bundle.execution_config import read_secret
from fiber_bundle.model_configuration import keychain
from fiber_bundle.model_configuration import store as models
from fiber_bundle.model_configuration.contracts import Change, Save
from fiber_bundle.model_configuration.errors import ModelError

pytest_plugins = ["test_model_configuration"]
PASSWORD = "synthetic-model-backup-password"


def test_managed_keys_roundtrip_into_new_keychain_and_keep_budget(
    config, store, tmp_path, vault_keychain
):
    with database.connect(config.state_root) as db:
        saved = models.save(
            config,
            db,
            Save(
                provider="openai",
                name="Synthetic",
                model="gpt-5",
                effort="high",
                api_key="synthetic-backup-key",
            ),
        )
        models.change(db, saved["id"], Change(action="default", expected_revision=1))
        old_path = db.execute("SELECT secret_file FROM model_credentials").fetchone()[0]
        with database.transaction(db):
            model_budgets.reserve(db, old_path, 700, 0)
        before_budget = model_budgets.inspect(db)
    before_keys = set(vault_keychain)
    marker = backup_bundle.create(config, tmp_path / "bundle", PASSWORD)
    assert marker["managed_model_credentials"] == "encrypted"
    for path in (tmp_path / "bundle").iterdir():
        assert b"synthetic-backup-key" not in path.read_bytes()
        assert all(k.encode() not in path.read_bytes() for k in vault_keychain)
    # Simulate a new device with no source account. The password-encrypted header suffices.
    vault_keychain.clear()
    target = Config(tmp_path / "restored", config.state_relative, config.vaults)
    result = backup_restore.restore(tmp_path / "bundle", target.project_root, PASSWORD)
    assert result["managed_model_credentials_restored"] == 1 and not result["automatic_start"]
    assert set(vault_keychain).isdisjoint(before_keys)
    backup_restore.activate(target, source_stopped=True)
    with database.connect(target.state_root) as db:
        catalog = models.inspect(db)
        assert catalog["default"]["configuration_id"] == saved["id"]
        assert catalog["models"][0]["effort"] == "high"
        assert model_budgets.inspect(db) == before_budget
        path = Path(db.execute("SELECT secret_file FROM model_credentials").fetchone()[0])
        assert path.is_relative_to(target.state_root)
        assert model_budgets.key(db, str(path)) == saved["credential_id"]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert read_secret(path) == "synthetic-backup-key"
    assert "synthetic-backup-key" not in json.dumps(catalog)


def test_keychain_failure_blocks_backup_and_restore_without_plaintext(
    config, store, tmp_path, vault_keychain, monkeypatch
):
    with database.connect(config.state_root) as db:
        models.save(
            config,
            db,
            Save(
                provider="openai", name="Synthetic", model="gpt-5", api_key="synthetic-backup-key"
            ),
        )
    backup_bundle.create(config, tmp_path / "bundle", PASSWORD)
    with pytest.raises(IntegrityError, match="authentication"):
        backup_restore.restore(
            tmp_path / "bundle", tmp_path / "bad-password", "incorrect-backup-password"
        )
    assert not (tmp_path / "bad-password").exists()
    monkeypatch.setattr(
        keychain, "put", lambda *a: (_ for _ in ()).throw(ModelError("model_keychain_unavailable"))
    )
    with pytest.raises(ModelError, match="keychain"):
        backup_restore.restore(tmp_path / "bundle", tmp_path / "locked", PASSWORD)
    with sqlite3.connect(tmp_path / "locked/.state/metadata.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
    assert (
        json.loads((tmp_path / "locked/.state/restore-gate.json").read_text())["state"] == "failed"
    )
    monkeypatch.setattr(
        keychain, "get", lambda *a: (_ for _ in ()).throw(ModelError("model_keychain_unavailable"))
    )
    with pytest.raises(ModelError, match="keychain"):
        backup_bundle.create(config, tmp_path / "blocked-export", PASSWORD)
    assert not (tmp_path / "blocked-export").exists()
