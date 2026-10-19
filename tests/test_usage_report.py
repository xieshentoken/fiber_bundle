"""Real SQLite reservations, key grouping, unknown costs and UTC date boundaries."""

import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from fiber_bundle import auth, database, execution_ledger, query_policy_store, usage_report
from fiber_bundle.agent_connections import cloud_budget, configuration
from fiber_bundle.agent_connections import store as agent_store
from fiber_bundle.agent_connections.contracts import ConnectionSpec
from fiber_bundle.config import InvalidInput
from fiber_bundle.execution_config import ExecutionConfig
from fiber_bundle.usage_credentials import reference


@pytest.fixture
def usage_policy(config, store):
    policy = ExecutionConfig(
        image="sha256:" + "a" * 64,
        endpoint="https://opencode.ai/zen/go/v1/chat/completions",
        model="synthetic-model",
        secret_file=str(config.project_root / "DO_NOT_READ.key"),
        price_revision="synthetic-only",
        input_nusd=150,
        output_nusd=500,
        max_input_tokens=16000,
        max_output_tokens=1024,
        max_request_bytes=65536,
        task_limit_nusd=4000000,
        daily_limit_nusd=400000000,
        max_calls=1,
        daily_calls=100,
        max_attempts=1,
        timeout_seconds=30,
        timezone="UTC",
    )
    query_policy_store.save(
        config,
        {
            "schema": "query-routing-preparation/v1",
            "enabled": False,
            "local_query": None,
            "cloud_answer": asdict(policy),
            "approved_backups": [],
        },
        None,
    )
    return policy


def stamp(value):
    return datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp()


def model_call(config, store, request_data, policy, when, usage):
    receipt = store.ingest(**request_data)
    db = database.connect(config.state_root)
    client = auth.create_client(db, "Synthetic usage", False)
    vault = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    auth.set_grant(db, client["id"], vault, query=True, original=True, add=False)
    run = execution_ledger.create_run(
        config, db, policy, client["id"], vault, receipt.source_version_id
    )
    execution_ledger.start(config, db, run, policy)
    call = execution_ledger.reserve(config, db, run, policy)
    if usage is not None:
        execution_ledger.settle(db, call, policy, usage)
    db.execute("UPDATE model_calls SET created_at=? WHERE id=?", (stamp(when), call))
    db.close()
    return call


def agent_call(config, policy, when, usage):
    db = database.connect(config.state_root)
    vault = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    row = configuration.create(
        config,
        db,
        ConnectionSpec(
            name="Synthetic usage Agent",
            agent_type="hermes",
            vault_ids=[vault],
            model_configuration_id="opencodego-primary",
        ),
        str(uuid4()),
    )
    with database.transaction(db):
        reservation = cloud_budget.admit(
            db, {**asdict(policy), "configuration_sha256": policy.digest}
        )
        op = agent_store.operation(
            db,
            row["id"],
            "test_query",
            str(uuid4()),
            {
                "cloud_reservation": reservation,
            },
        )
    agent_store.finish(
        db,
        op["id"],
        "succeeded",
        {
            "usage_state": "reported" if usage else "unknown",
            "usage": usage,
            "cloud_cost_nusd": cloud_budget.charged(reservation, usage),
        },
    )
    db.execute(
        "UPDATE agent_connection_operations SET created_at=? WHERE id=?", (stamp(when), op["id"])
    )
    db.close()


def test_usage_groups_keys_without_reading_credentials_and_never_backfills_history(
    config,
    store,
    request_data,
    usage_policy,
    monkeypatch,
):
    a = usage_policy
    b = replace(a, secret_file=str(config.project_root / "OTHER_DO_NOT_READ.key"))
    model_call(
        config,
        store,
        request_data,
        a,
        "2026-10-01T00:00:00",
        {"prompt_tokens": 100, "completion_tokens": 20},
    )
    model_call(
        config,
        store,
        request_data,
        b,
        "2026-10-01T23:59:59",
        {"prompt_tokens": 200, "completion_tokens": 40},
    )
    model_call(config, store, request_data, a, "2026-10-02T12:00:00", None)
    legacy = model_call(
        config,
        store,
        request_data,
        a,
        "2026-10-03T23:59:59",
        {"prompt_tokens": 20, "completion_tokens": 4},
    )
    agent_call(config, b, "2026-10-02T00:00:00", {"prompt_tokens": 100, "completion_tokens": 20})
    agent_call(config, b, "2026-10-02T01:00:00", None)
    db = database.connect(config.state_root)
    db.execute("DELETE FROM model_call_credentials WHERE call_id=?", (legacy,))
    before = [tuple(row) for row in db.execute("SELECT * FROM model_calls")]

    def never_read(*args, **kwargs):
        raise AssertionError("Usage must not load API secrets")

    monkeypatch.setattr("fiber_bundle.execution_config.read_secret", never_read)
    result = usage_report.report(config, db, start="2026-10-01", end="2026-10-03")
    assert result["summary"] == {
        "calls": 6,
        "settled_calls": 4,
        "unsettled_calls": 2,
        "price_unknown_calls": 0,
        "pending_calls": 1,
        "result_unknown_calls": 1,
        "estimated_nusd": 105000,
        "reserved_nusd": 2 * a.reserve_nusd,
        "input_tokens": 420,
        "output_tokens": 84,
        "token_unknown_calls": 2,
    }
    assert [row["calls"] for row in result["daily"]] == [2, 3, 1]
    assert {row["id"]: row["calls"] for row in result["by_key"]} == {
        reference(a.secret_file)["id"]: 2,
        reference(b.secret_file)["id"]: 3,
        "unattributed": 1,
    }
    filtered = usage_report.report(
        config, db, start="2026-10-01", end="2026-10-03", api_key_id=reference(b.secret_file)["id"]
    )
    assert filtered["summary"]["calls"] == 3
    assert filtered["summary"]["estimated_nusd"] == 75000
    assert filtered["summary"]["reserved_nusd"] == b.reserve_nusd
    assert [tuple(row) for row in db.execute("SELECT * FROM model_calls")] == before
    wire = json.dumps(result)
    assert "secret_file" not in wire and str(config.project_root) not in wire
    assert "DO_NOT_READ" not in wire
    db.close()


def test_zero_usage_key_is_visible_and_dates_are_inclusive_without_fake_data(
    config,
    store,
    request_data,
    usage_policy,
):
    model_call(
        config,
        store,
        request_data,
        usage_policy,
        "2026-10-04T00:00:00",
        {"prompt_tokens": 100, "completion_tokens": 20},
    )
    db = database.connect(config.state_root)
    result = usage_report.report(config, db, start="2026-10-01", end="2026-10-03")
    assert len(result["keys"]) == 1 and result["keys"][0]["configured"]
    assert result["by_key"][0]["calls"] == 0
    assert result["summary"]["estimated_nusd"] == 0
    assert len(result["daily"]) == 3 and all(day["calls"] == 0 for day in result["daily"])
    assert len(usage_report.report(config, db)["daily"]) == 30
    db.close()


@pytest.mark.parametrize(
    "start,end,key",
    [
        ("2026-10-04", "2026-10-01", "all"),
        ("2026-02-30", "2026-10-01", "all"),
        ("2025-01-01", "2026-10-01", "all"),
        ("20261001", "2026-10-03", "all"),
        ("2026-10-01", "2026-10-03", "' OR 1=1"),
        ("2026-10-01", "2026-10-03", "key_" + "0" * 64),
    ],
)
def test_usage_validates_filters(config, usage_policy, start, end, key):
    db = database.connect(config.state_root)
    with pytest.raises(InvalidInput):
        usage_report.report(config, db, start=start, end=end, api_key_id=key)
    db.close()


def test_usage_endpoint_is_read_only_and_admin_only(http):
    result = http.get("/admin/system/usage?start=2026-10-01&end=2026-10-03")
    assert result.status_code == 200
    assert result.json()["cost_basis"] == "configured_price_estimate"
    assert result.json()["summary"]["calls"] == 0
    assert "no-store" in result.headers["cache-control"]
    assert http.get("/admin/system/usage?start=2026-10-04&end=2026-10-01").status_code == 422
    http.cookies.clear()
    assert http.get("/admin/system/usage").status_code == 401


def test_price_unknown_is_not_pending_or_zero_cost(config, store, request_data, usage_policy):
    call = model_call(
        config,
        store,
        request_data,
        usage_policy,
        "2026-10-02T00:00:00",
        {"prompt_tokens": 1, "completion_tokens": 1},
    )
    db = database.connect(config.state_root)
    db.execute("UPDATE model_calls SET charged_nusd=NULL WHERE id=?", (call,))
    result = usage_report.report(config, db, start="2026-10-01", end="2026-10-03")
    assert result["summary"]["price_unknown_calls"] == 1
    assert result["summary"]["pending_calls"] == 0
    assert result["summary"]["result_unknown_calls"] == 0
    assert result["summary"]["unsettled_calls"] == 1
    assert result["summary"]["settled_calls"] == 0


def test_lifetime_covers_old_calls_without_changing_range_or_ledger(
    config, store, request_data, usage_policy
):
    model_call(
        config,
        store,
        request_data,
        usage_policy,
        "2025-01-01T12:00:00",
        {"prompt_tokens": 1000, "completion_tokens": 200},
    )
    model_call(
        config,
        store,
        request_data,
        usage_policy,
        "2026-10-01T12:00:00",
        {"prompt_tokens": 100, "completion_tokens": 20},
    )
    model_call(config, store, request_data, usage_policy, "2026-10-02T12:00:00", None)
    with database.connect(config.state_root) as db:
        before = [tuple(r) for r in db.execute("SELECT * FROM model_calls")]
        data = usage_report.report(config, db, start="2026-10-01", end="2026-10-02")
        assert data["summary"]["calls"] == 2
        assert data["summary"]["input_tokens"] == 100
        assert data["lifetime"]["calls"] == 3
        assert data["lifetime"]["input_tokens"] == 1100
        assert data["lifetime"]["output_tokens"] == 220
        assert data["lifetime"]["token_unknown_calls"] == 1
        assert data["lifetime"]["estimated_nusd"] > data["summary"]["estimated_nusd"]
        filtered = usage_report.report(
            config,
            db,
            start="2026-10-01",
            end="2026-10-02",
            api_key_id=reference(usage_policy.secret_file)["id"],
        )
        assert filtered["lifetime"] == data["lifetime"]
        assert [tuple(r) for r in db.execute("SELECT * FROM model_calls")] == before


def test_empty_lifetime_is_zero_not_missing(config, store):
    with database.connect(config.state_root) as db:
        data = usage_report.report(config, db)
        assert data["lifetime"]["calls"] == data["lifetime"]["input_tokens"] == 0
        assert data["lifetime"]["token_unknown_calls"] == 0
