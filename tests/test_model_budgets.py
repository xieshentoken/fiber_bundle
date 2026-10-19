"""Shared budgets exercise real atomic reservations, history and authenticated settings."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from fiber_bundle import database, model_budgets, usage_credentials
from fiber_bundle import execution_ledger as ledger
from fiber_bundle.config import InvalidInput

pytest_plugins = ["test_execution"]


def save(db, path, limit=200000, **extra):
    return model_budgets.save(
        db,
        model_budgets.Save(
            secret_file=path,
            settings=model_budgets.Policy(label="Synthetic key", limit=limit),
            expected_revision=extra.pop("expected_revision", 0),
            **extra,
        ),
    )


def test_real_model_ledger_uses_shared_tokens_and_unknown_usage_retains_budget(
    config, policy, bound
):
    run, _, _ = bound
    with database.connect(config.state_root) as db:
        save(db, policy.secret_file)
        ledger.start(config, db, run, policy)
        call = ledger.reserve(config, db, run, policy)
        assert (
            model_budgets.inspect(db)[0]["used"]
            == policy.max_input_tokens + policy.max_output_tokens
        )
        ledger.interrupt(db, run)
        with pytest.raises(InvalidInput, match="missing usage"):
            ledger.settle(db, call, policy, None)
        assert model_budgets.inspect(db)[0]["remaining"] < 200000
        ledger.settle(
            db, call, policy, {"prompt_tokens": 12, "completion_tokens": 8}, reconcile=True
        )
        assert model_budgets.inspect(db)[0]["used"] == 20


def test_concurrent_reservations_share_one_atomic_pool(config, store, tmp_path):
    secret = str(tmp_path / "key")
    with database.connect(config.state_root) as db:
        save(db, secret, limit=100)

    def reserve(_):
        try:
            with database.connect(config.state_root) as db, database.transaction(db):
                return model_budgets.reserve(db, secret, 80, 5)
        except InvalidInput:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(bool(v) for v in pool.map(reserve, range(2))) == 1


def test_rotation_rename_and_history_do_not_reset_consumption(config, policy, bound, tmp_path):
    run, _, _ = bound
    with database.connect(config.state_root) as db:
        ledger.start(config, db, run, policy)
        call = ledger.reserve(config, db, run, policy)
        ledger.settle(db, call, policy, {"prompt_tokens": 13, "completion_tokens": 7})
        original = save(db, policy.secret_file)
        assert original["used"] == 20
        rotated = save(
            db,
            str(tmp_path / "rotated-key"),
            credential_id=original["credential_id"],
            expected_revision=1,
        )
        assert rotated["used"] == 20
        assert (
            usage_credentials.reference(str(tmp_path / "rotated-key"), db)["id"]
            == original["credential_id"]
        )
        assert model_budgets.inspect(db)[0]["provider_balance"] is None
        with pytest.raises(InvalidInput, match="revision_changed"):
            save(db, policy.secret_file)


def test_budget_settings_require_admin_csrf_and_do_not_load_secrets(http, tmp_path):
    path = tmp_path / "not-created-secret"
    body = {
        "secret_file": str(path),
        "expected_revision": 0,
        "settings": {"label": "Synthetic key", "limit": 1000},
    }
    assert (
        http.put(
            "/admin/system/model-budgets", json=body, headers={"X-CSRF-Token": "wrong"}
        ).status_code
        == 403
    )
    response = http.put("/admin/system/model-budgets", json=body)
    assert response.status_code == 200, response.text
    assert not path.exists()
    assert "secret_file" not in http.get("/admin/system/model-budgets").text
    http.cookies.clear()
    assert http.get("/admin/system/model-budgets").status_code == 401


def test_schema31_migration_backs_up_and_preserves_existing_vault_rows(config, store):
    import sqlite3

    with database.connect(config.state_root) as db:
        before = [tuple(r) for r in db.execute("SELECT * FROM vaults")]
        for table in (
            "model_configuration_calls",
            "model_configurations",
            "model_credentials",
            "model_configuration_settings",
            "video_tasks",
            "pi_processing_runs",
            "model_call_budget_bindings",
            "model_budget_reservations",
            "model_credential_aliases",
            "model_budget_policies",
        ):
            db.execute("DROP TABLE " + table)
        db.execute("PRAGMA user_version=31")
    backup = database.migrate(config)
    assert backup is not None
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 31
    with database.connect(config.state_root) as db:
        assert [tuple(r) for r in db.execute("SELECT * FROM vaults")] == before
        assert db.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
        assert model_budgets.inspect(db) == []
