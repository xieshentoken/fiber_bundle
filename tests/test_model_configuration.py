"""Observable model catalog, secret-generation and schema compatibility behavior."""

import json
import sqlite3
from pathlib import Path

import pytest

from fiber_bundle import database, model_budgets
from fiber_bundle.execution_config import read_secret
from fiber_bundle.model_configuration import keychain, secrets
from fiber_bundle.model_configuration import store as catalog
from fiber_bundle.model_configuration.contracts import Change, Save
from fiber_bundle.model_configuration.errors import ModelError


@pytest.fixture
def vault_keychain(monkeypatch):
    values = {}
    monkeypatch.setattr(keychain, "put", lambda account, value: values.__setitem__(account, value))
    monkeypatch.setattr(keychain, "get", lambda account: values[account])
    return values


def spec(**kw):
    return Save(
        provider="openai",
        name="Synthetic account",
        model="gpt-5",
        api_key="synthetic-model-key+/=",
        **kw,
    )


def test_save_encrypts_and_does_not_publish_key(config, store, vault_keychain):
    with database.connect(config.state_root) as db:
        saved = catalog.save(config, db, spec())
        public = json.dumps(catalog.inspect(db))
        assert "synthetic-model-key" not in public
        assert "ciphertext" not in public and "fingerprint" not in public
        assert saved["verification"] is None
        secret = Path(db.execute("SELECT secret_file FROM model_credentials").fetchone()[0])
    assert "synthetic-model-key" not in secret.read_text()
    assert secret.stat().st_mode & 0o777 == 0o600
    assert read_secret(secret) == "synthetic-model-key+/="


def test_reuse_rename_and_rotation_keep_shared_budget(config, store, vault_keychain):
    with database.connect(config.state_root) as db:
        first = catalog.save(config, db, spec())
        cid = first["credential_id"]
        old_path = db.execute(
            "SELECT secret_file FROM model_credentials WHERE id=?", (cid,)
        ).fetchone()[0]
        with database.transaction(db):
            model_budgets.reserve(db, old_path, 70, 0)
        second = catalog.save(
            config,
            db,
            Save(
                provider="openai",
                name="Renamed",
                model="gpt-5-mini",
                credential_id=cid,
                credential_revision=1,
            ),
        )
        assert second["credential_id"] == cid
        assert model_budgets.inspect(db)[0]["used"] == 70
        rotated = catalog.save(
            config,
            db,
            Save(
                provider="openai",
                name="Renamed",
                model="gpt-5",
                api_key="synthetic-replacement-key",
                credential_id=cid,
                credential_revision=2,
                configuration_id=first["id"],
                expected_revision=1,
            ),
        )
        new_path = db.execute(
            "SELECT secret_file FROM model_credentials WHERE id=?", (cid,)
        ).fetchone()[0]
        assert rotated["credential_id"] == cid and new_path != old_path
        assert model_budgets.inspect(db)[0]["used"] == 70
    with pytest.raises(ModelError, match="revoked"):
        read_secret(Path(old_path))
    assert read_secret(Path(new_path)) == "synthetic-replacement-key"


def test_duplicate_key_uses_one_budget(config, store, vault_keychain):
    with database.connect(config.state_root) as db:
        a = catalog.save(config, db, spec())
        b = catalog.save(config, db, spec())
        assert a["credential_id"] == b["credential_id"]
        assert len(catalog.inspect(db)["credentials"]) == 1


def test_conflict_and_secret_failure_leave_no_partial_configuration(
    config, store, vault_keychain, monkeypatch
):
    with database.connect(config.state_root) as db:
        saved = catalog.save(config, db, spec())
        before = catalog.inspect(db)
        with pytest.raises(ModelError, match="conflict"):
            catalog.save(
                config,
                db,
                Save(
                    **{
                        **spec().model_dump(),
                        "model": "gpt-5-mini",
                        "configuration_id": saved["id"],
                        "expected_revision": 0,
                    }
                ),
            )
        assert catalog.inspect(db) == before
        monkeypatch.setattr(
            secrets,
            "seal",
            lambda *a: (_ for _ in ()).throw(ModelError("model_keychain_unavailable")),
        )
        with pytest.raises(ModelError, match="keychain"):
            catalog.save(
                config,
                db,
                Save(
                    provider="deepseek",
                    name="Other",
                    model="deepseek-chat",
                    api_key="synthetic-other-key",
                ),
            )
        assert catalog.inspect(db) == before


def test_default_references_and_old_profile_kept(config, store, vault_keychain):
    with database.connect(config.state_root) as db:
        a = catalog.save(config, db, spec())
        catalog.change(db, a["id"], Change(action="default", expected_revision=1))
        with pytest.raises(ModelError, match="referenced"):
            catalog.change(db, a["id"], Change(action="delete", expected_revision=1))
        b = catalog.save(config, db, spec())
        catalog.change(db, b["id"], Change(action="default", expected_revision=1))
        catalog.change(db, a["id"], Change(action="disable", expected_revision=1))
        assert catalog.get(db, a["id"])["active"] == 0
        assert catalog.default(db)["configuration_id"] == b["id"]


def test_schema33_migrates_without_rewriting_old_tables(config, store):
    path = config.state_root / "metadata.sqlite3"
    with sqlite3.connect(path) as db:
        before = db.execute("SELECT * FROM vaults").fetchall()
        for table in (
            "model_configuration_calls",
            "model_configurations",
            "model_credentials",
            "model_configuration_settings",
        ):
            db.execute("DROP TABLE " + table)
        db.execute("PRAGMA user_version=33")
    assert database.migrate(config) is not None
    with database.connect(config.state_root) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
        assert [tuple(r) for r in db.execute("SELECT * FROM vaults")] == before
        assert catalog.inspect(db)["models"] == []


def test_saved_effort_is_checked_not_silently_ignored(config, store, vault_keychain):
    with database.connect(config.state_root) as db:
        with pytest.raises(ModelError, match="effort_unsupported"):
            catalog.save(config, db, spec(effort="imaginary"))
        assert catalog.inspect(db)["models"] == []


def test_client_identity_retry_and_default_compare_and_swap(config, store, vault_keychain):
    with database.connect(config.state_root) as db:
        value = spec(configuration_id="model_" + "b" * 32)
        first = catalog.save(config, db, value)
        assert catalog.save(config, db, value) == first
        assert len(catalog.inspect(db)["models"]) == 1
        catalog.change(
            db,
            first["id"],
            Change(action="default", expected_revision=1, expected_default_revision=0),
        )
        with pytest.raises(ModelError, match="conflict"):
            catalog.change(
                db,
                first["id"],
                Change(action="default", expected_revision=1, expected_default_revision=0),
            )


def test_rotating_shared_key_invalidates_other_model_verification(config, store, vault_keychain):
    with database.connect(config.state_root) as db:
        first = catalog.save(config, db, spec())
        second = catalog.save(config, db, spec())
        db.execute(
            "UPDATE model_configurations SET verification_json=? WHERE id=?",
            (
                json.dumps(
                    {"state": "passed", "credential_generation": 1, "configuration_revision": 1}
                ),
                second["id"],
            ),
        )
        assert catalog.inspect(db)["models"][1]["verification"]["state"] == "passed"
        catalog.save(
            config,
            db,
            Save(
                **{
                    **spec().model_dump(),
                    "configuration_id": first["id"],
                    "expected_revision": 1,
                    "credential_id": first["credential_id"],
                    "credential_revision": 1,
                    "api_key": "synthetic-rotated-key",
                }
            ),
        )
        assert catalog.inspect(db)["models"][1]["verification"] is None


def test_legacy_file_key_adopts_existing_budget_without_reset(config, store, vault_keychain):
    from fiber_bundle import query_policy_store

    secret = config.project_root / "legacy-model.key"
    secret.write_text("synthetic-legacy-key")
    secret.chmod(0o600)
    query_policy_store.save(
        config,
        {
            "schema": "query-routing-preparation/v1",
            "enabled": False,
            "local_query": {
                "image": "sha256:" + "a" * 64,
                "endpoint": "http://127.0.0.1:8001/v1/chat/completions",
                "model": "synthetic-legacy",
                "secret_file": str(secret),
                "max_input_tokens": 8192,
                "max_output_tokens": 1024,
                "max_request_bytes": 65536,
                "max_calls": 1,
                "daily_calls": 30,
                "timeout_seconds": 30,
            },
            "cloud_answer": None,
            "approved_backups": [],
        },
        None,
    )
    with database.connect(config.state_root) as db:
        old = model_budgets.save(
            db,
            model_budgets.Save(
                secret_file=str(secret),
                expected_revision=0,
                settings=model_budgets.Policy(label="Legacy", limit=1000),
            ),
        )
        with database.transaction(db):
            model_budgets.reserve(db, str(secret), 70, 0)
        profile = catalog.save(
            config,
            db,
            Save(
                provider="local",
                name="Managed legacy",
                base_url="http://127.0.0.1:8001/v1",
                model="synthetic/model",
                api_key="synthetic-legacy-key",
            ),
        )
        assert profile["credential_id"] == old["credential_id"]
        budget = model_budgets.inspect(db)[0]
        assert budget["settings"]["limit"] == 1000 and budget["used"] == 70
        assert model_budgets.key(db, str(secret)) == profile["credential_id"]
    assert secret.read_text() == "synthetic-legacy-key"
    assert query_policy_store.legacy_current(config).local_query.model == "synthetic-legacy"
