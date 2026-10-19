import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from fiber_bundle import auth, database, execution, model_gateway
from fiber_bundle import execution_ledger as ledger
from fiber_bundle.config import InvalidInput
from fiber_bundle.execution_config import ExecutionConfig, load_execution_config, read_secret


@pytest.fixture
def policy(tmp_path):
    return ExecutionConfig(
        image="sha256:" + "a" * 64,
        endpoint="https://opencode.ai/zen/go/v1/chat/completions",
        model="glm-5.3-flash",
        secret_file=str(tmp_path / "secret"),
        price_revision="synthetic-v1",
        input_nusd=150,
        output_nusd=500,
        max_input_tokens=131072,
        max_output_tokens=512,
        max_request_bytes=65536,
        task_limit_nusd=100000000,
        daily_limit_nusd=100000000,
        max_calls=3,
        daily_calls=3,
        max_attempts=2,
        timeout_seconds=120,
        timezone="UTC",
    )


@pytest.fixture
def bound(config, store, request_data, policy):
    receipt = store.ingest(**request_data)
    db = database.connect(config.state_root)
    c = auth.create_client(db, "synthetic-executor", False)
    vid = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    auth.set_grant(db, c["id"], vid, query=True, original=True, add=False)
    run_id = ledger.create_run(config, db, policy, c["id"], vid, receipt.source_version_id)
    db.close()
    return run_id, c["id"], vid


@pytest.mark.parametrize(
    "field,value",
    [
        ("input_nusd", 0),
        ("output_nusd", None),
        ("daily_calls", 0),
        ("task_limit_nusd", True),
        ("max_attempts", 4),
        ("timeout_seconds", 0),
        ("endpoint", "https://example.com/steal"),
        ("image", "node:latest"),
    ],
)
def test_fail_closed_configuration(policy, field, value):
    with pytest.raises(InvalidInput):
        replace(policy, **{field: value}).validate()


def test_bad_config_file(tmp_path):
    p = tmp_path / "config.json"
    p.write_text('{"endpoint":"https://example.com"}')
    with pytest.raises(InvalidInput):
        load_execution_config(p)


def test_secret_no_ambiguous_content_or_symlink(tmp_path):
    p = tmp_path / "key.txt"
    p.write_text("sk-" + "x" * 32)
    assert read_secret(p).startswith("sk-")
    q = tmp_path / "link"
    q.symlink_to(p)
    with pytest.raises(InvalidInput):
        read_secret(q)
    p.write_text("sk-" + "x" * 32 + "\nsk-" + "y" * 32)
    with pytest.raises(InvalidInput):
        read_secret(p)


def test_v2_migration_keeps_tables_and_backup(config):
    config.state_root.mkdir()
    db = sqlite3.connect(config.state_root / "metadata.sqlite3")
    db.executescript(database.SCHEMA + database.SCHEMA_V2)
    db.execute("PRAGMA user_version=2")
    db.close()
    backup = database.migrate(config)
    assert sqlite3.connect(backup).execute("PRAGMA user_version").fetchone()[0] == 2
    db = database.connect(config.state_root)
    assert db.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    layout_tables = {"vault_save_policies", "vault_directory_previews", "publication_locations"}
    assert layout_tables <= tables
    automatic_tables = {"automatic_policies", "automatic_jobs", "automatic_reviews"}
    assert automatic_tables <= tables
    assert all(not db.execute("SELECT * FROM " + table).fetchall() for table in automatic_tables)
    budget_tables = {
        "video_tasks",
        "pi_processing_runs",
        "model_call_budget_bindings",
        "model_budget_reservations",
        "model_credential_aliases",
        "model_budget_policies",
    }
    assert budget_tables <= tables
    assert all(not db.execute("SELECT * FROM " + table).fetchall() for table in budget_tables)
    model_tables = {
        "model_credentials",
        "model_configurations",
        "model_configuration_settings",
        "model_configuration_calls",
    }
    assert model_tables <= tables
    assert len(tables - layout_tables - automatic_tables - budget_tables - model_tables) == 55
    assert {"system_settings", "model_call_credentials"} <= tables
    assert {"vault_admissions", "vault_operations"} <= tables
    for table in (
        "document_creation_sessions",
        "document_creation_calls",
        "document_creation_artifacts",
    ):
        assert not db.execute("SELECT * FROM " + table).fetchall()
    assert db.execute("SELECT count(*) FROM cloud_answer_tasks").fetchone()[0] == 0
    db.close()


def test_reservation_retained_recovery_no_replay(config, policy, bound):
    run_id, _, _ = bound
    db = database.connect(config.state_root)
    ledger.start(config, db, run_id, policy)
    call = ledger.reserve(config, db, run_id, policy)
    ledger.interrupt(db, run_id)
    row = db.execute("SELECT * FROM model_calls WHERE id=?", (call,)).fetchone()
    assert row["state"] == "uncertain" and row["charged_nusd"] is None
    with pytest.raises(InvalidInput):
        ledger.start(config, db, run_id, policy)
    db.close()


def test_concurrent_budget_reservations(config, policy, bound):
    run_id, _, _ = bound
    db = database.connect(config.state_root)
    ledger.start(config, db, run_id, policy)
    db.close()

    def reserve(_):
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                ledger.reserve(config, db, run_id, policy)
                return True
            except ledger.BudgetExceeded:
                return False
            finally:
                db.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(reserve, range(8))) == 3


def test_unknown_usage_and_overage_are_not_free(config, policy, bound):
    db = database.connect(config.state_root)
    ledger.start(config, db, bound[0], policy)
    call = ledger.reserve(config, db, bound[0], policy)
    with pytest.raises(InvalidInput):
        ledger.settle(db, call, policy, {})
    assert db.execute("SELECT charged_nusd FROM model_calls").fetchone()[0] is None
    with pytest.raises(InvalidInput):
        ledger.settle(db, call, policy, {"prompt_tokens": 12, "completion_tokens": 600})
    assert db.execute("SELECT charged_nusd FROM model_calls").fetchone()[0] == 12 * 150 + 600 * 500
    db.close()


def test_revocation_denies_backend_and_model_before_call(config, policy, bound):
    db = database.connect(config.state_root)
    ledger.start(config, db, bound[0], policy)
    auth.revoke_client(db, bound[1])
    db.close()
    with patch.object(model_gateway, "complete") as remote:
        with pytest.raises(auth.Denied):
            execution.handle(
                config, policy, bound[0], {"type": "model", "id": "1", "body": {"messages": []}}
            )
        remote.assert_not_called()


def test_backend_scope_bound_no_client_supplied_scope(config, policy, bound):
    db = database.connect(config.state_root)
    ledger.start(config, db, bound[0], policy)
    db.close()
    reply = execution.handle(config, policy, bound[0], {"type": "vaults", "id": "1", "body": {}})
    assert len(reply["body"]) == 1 and reply["body"][0]["id"] == bound[2]
    with pytest.raises(InvalidInput):
        execution.handle(
            config, policy, bound[0], {"type": "vaults", "id": "2", "body": {"vault_id": "other"}}
        )


def test_model_limits_ignore_untrusted_fields(policy):
    result = model_gateway.prepare(
        policy,
        {
            "messages": [{"role": "user", "content": "synthetic"}],
            "max_tokens": 999999,
            "model": "other",
            "api_key": "do-not-forward",
            "stream": True,
        },
    )
    assert result["max_tokens"] == 512 and result["model"] == policy.model
    assert "api_key" not in result and result["stream"] is False


def test_cancel_is_terminal_and_no_replay(config, policy, bound):
    db = database.connect(config.state_root)
    ledger.start(config, db, bound[0], policy)
    ledger.reserve(config, db, bound[0], policy)
    db.close()
    with patch.object(execution, "stop"):
        execution.cancel(config, bound[0])
    db = database.connect(config.state_root)
    ledger.transition(db, bound[0], "succeeded")
    assert ledger.get_run(db, bound[0])["state"] == "cancelled"
    with pytest.raises(InvalidInput):
        ledger.start(config, db, bound[0], policy)
    db.close()


def test_safe_restart_before_any_model_call_and_attempt_limit(config, policy, bound):
    db = database.connect(config.state_root)
    for _ in range(2):
        ledger.start(config, db, bound[0], policy)
        ledger.interrupt(db, bound[0])
    with pytest.raises(InvalidInput):
        ledger.start(config, db, bound[0], policy)
    db.close()


def test_command_mounts_one_leaf_and_no_host_capabilities(policy, tmp_path, bound):
    cmd = execution.command(policy, tmp_path, bound[0])
    assert cmd.count("--mount") == 1
    assert "--read-only" in cmd and "--privileged" not in cmd
    assert "--publish" not in cmd and "--env" not in cmd
    assert cmd[-1] == policy.image
    assert policy.secret_file not in json.dumps(cmd)


def test_run_lock_blocks_concurrent_owner(config, bound):
    with execution.run_lock(config, bound[0]):
        with pytest.raises(InvalidInput):
            with execution.run_lock(config, bound[0]):
                pytest.fail("second owner acquired lock")


def test_pi_text_blocks_normalized_without_mutating_input(policy):
    raw = {"messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]}
    assert model_gateway.prepare(policy, raw)["messages"][0]["content"] == "hello"
    assert isinstance(raw["messages"][0]["content"], list)
    with pytest.raises(InvalidInput):
        model_gateway.prepare(
            policy, {"messages": [{"role": "user", "content": [{"type": "image_url"}]}]}
        )


def test_task_money_limit_blocks_even_when_call_count_available(config, policy, bound):
    db = database.connect(config.state_root)
    limited = replace(policy, task_limit_nusd=1)
    version = ledger.get_run(db, bound[0])["version_id"]
    rid = ledger.create_run(config, db, limited, bound[1], bound[2], version)
    ledger.start(config, db, rid, limited)
    with pytest.raises(ledger.BudgetExceeded):
        ledger.reserve(config, db, rid, limited)
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    db.close()


def test_daily_money_limit_is_shared_across_runs(config, policy, bound):
    db = database.connect(config.state_root)
    limited = replace(policy, daily_limit_nusd=policy.reserve_nusd)
    version = ledger.get_run(db, bound[0])["version_id"]
    for index in range(2):
        rid = ledger.create_run(config, db, limited, bound[1], bound[2], version)
        ledger.start(config, db, rid, limited)
        if index == 0:
            ledger.reserve(config, db, rid, limited)
        else:
            with pytest.raises(ledger.BudgetExceeded):
                ledger.reserve(config, db, rid, limited)
    db.close()


def test_v2_to_v3_failure_preserves_v2(config):
    config.state_root.mkdir()
    db = sqlite3.connect(config.state_root / "metadata.sqlite3")
    db.executescript(database.SCHEMA + database.SCHEMA_V2)
    db.execute("PRAGMA user_version=2")
    db.close()
    with patch.object(database, "SCHEMA_V3", database.SCHEMA_V3 + "INVALID SQL;"):
        with pytest.raises(sqlite3.Error):
            database.migrate(config)
    db = sqlite3.connect(config.state_root / "metadata.sqlite3")
    assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    assert len(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()) == 11
    db.close()


def test_cancel_during_provider_call_keeps_charge_and_discards_response(config, policy, bound):
    db = database.connect(config.state_root)
    ledger.start(config, db, bound[0], policy)
    db.close()

    def remote(*args):
        with patch.object(execution, "stop"):
            execution.cancel(config, bound[0])
        return {
            "choices": [{"message": {"role": "assistant", "content": "synthetic"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        }

    with patch.object(model_gateway, "complete", side_effect=remote):
        with pytest.raises(InvalidInput):
            execution.handle(
                config,
                policy,
                bound[0],
                {
                    "type": "model",
                    "id": "1",
                    "body": {"messages": [{"role": "user", "content": "synthetic"}]},
                },
            )
    db = database.connect(config.state_root)
    assert ledger.get_run(db, bound[0])["state"] == "cancelled"
    assert db.execute("SELECT charged_nusd FROM model_calls").fetchone()[0] == 2500
    db.close()


def test_model_failure_does_not_refund_reservation(config, policy, bound):
    db = database.connect(config.state_root)
    ledger.start(config, db, bound[0], policy)
    db.close()
    with patch.object(model_gateway, "complete", side_effect=InvalidInput("synthetic failure")):
        with pytest.raises(InvalidInput):
            execution.handle(
                config,
                policy,
                bound[0],
                {
                    "type": "model",
                    "id": "1",
                    "body": {"messages": [{"role": "user", "content": "synthetic"}]},
                },
            )
    db = database.connect(config.state_root)
    row = db.execute("SELECT state,reserved_nusd,charged_nusd FROM model_calls").fetchone()
    assert tuple(row) == ("uncertain", policy.reserve_nusd, None)
    db.close()


def test_provider_http_error_retains_status_without_secret(policy, tmp_path):
    import urllib.error

    keyfile = Path(policy.secret_file)
    keyfile.write_text("synthetic-secret-012345678901234567890")
    opener = type(
        "Reject",
        (),
        {
            "open": lambda *a, **kw: (_ for _ in ()).throw(
                urllib.error.HTTPError(policy.endpoint, 429, "PRIVATE PROVIDER BODY", {}, None)
            )
        },
    )()
    with patch.object(model_gateway.urllib.request, "build_opener", return_value=opener):
        with pytest.raises(model_gateway.ModelFailure) as caught:
            model_gateway.complete(policy, "synthetic-run", {"messages": []})
    assert caught.value.code == "model_http_429"
    assert "PRIVATE" not in str(caught.value)
