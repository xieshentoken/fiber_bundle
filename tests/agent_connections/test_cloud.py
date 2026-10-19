"""Explicit cloud references, real shared ledgers and the installed Hermes boundary."""

import json
import os
import subprocess
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import pytest

from fiber_bundle import auth, database, execution_ledger, local_query_budget, query_policy_store
from fiber_bundle.agent_connections import cloud_budget, configuration, models
from fiber_bundle.agent_connections import store as agent_store
from fiber_bundle.agent_connections.contracts import ConnectionSpec
from fiber_bundle.config import InvalidInput
from fiber_bundle.execution_config import ExecutionConfig


@pytest.fixture
def cloud(config, store):
    policy = ExecutionConfig(
        image="sha256:" + "a" * 64,
        endpoint="https://opencode.ai/zen/go/v1/chat/completions",
        model="synthetic-cloud",
        secret_file=str(config.project_root / "not-read.key"),
        price_revision="synthetic-only",
        input_nusd=1,
        output_nusd=1,
        max_input_tokens=16000,
        max_output_tokens=1024,
        max_request_bytes=65536,
        task_limit_nusd=20000,
        daily_limit_nusd=40000,
        max_calls=1,
        daily_calls=2,
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


def test_cloud_catalog_is_explicit_and_never_reads_credentials(config, cloud):
    catalog = models.available(config)
    assert len(catalog) == 1 and catalog[0]["id"] == "opencodego-primary"
    assert catalog[0]["processing_location"] == "cloud"
    assert "secret_file" not in catalog[0]
    assert models.resolve(config, "opencodego-primary")["model"] == cloud.model
    with pytest.raises(InvalidInput):
        models.resolve(config, "backup-1")
    with pytest.raises(InvalidInput):
        models.resolve(config, "local-primary")
    assert not query_policy_store.inspect(config)["inference_enabled"]


def cloud_intent(config, db, model, request):
    vid = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    connection = configuration.create(
        config,
        db,
        ConnectionSpec(
            name=request,
            agent_type="hermes",
            vault_ids=[vid],
            model_configuration_id="opencodego-primary",
        ),
        request + "-create",
    )
    with database.transaction(db):
        admission = cloud_budget.admit(db, model)
        return agent_store.operation(
            db, connection["id"], "test_query", request, {"cloud_reservation": admission}
        )


def bound_run(config, db, store_fixture, request_data, policy):
    receipt = store_fixture.ingest(**request_data)
    client = auth.create_client(db, "Synthetic cloud budget", False)
    vid = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    auth.set_grant(db, client["id"], vid, query=True, original=True, add=False)
    return execution_ledger.create_run(
        config, db, policy, client["id"], vid, receipt.source_version_id
    )


def test_cloud_agent_reservations_block_builtin_and_local_budget_is_separate(
    config, store, request_data, cloud
):
    db = database.connect(config.state_root)
    try:
        model = models.resolve(config, "opencodego-primary")
        one = cloud_intent(config, db, model, "one")
        agent_store.finish(db, one["id"], "failed", error="synthetic-provider-failure")
        cloud_intent(config, db, model, "two")
        day = json.loads(one["payload_json"])["cloud_reservation"]["billing_day"]
        assert cloud_budget.totals(db, day) == (2, 34048)
        assert local_query_budget.count(db, day) == 0
        with pytest.raises(InvalidInput, match="agent_cloud_budget_exhausted"):
            cloud_budget.admit(db, model)
        run_id = bound_run(config, db, store, request_data, cloud)
        with pytest.raises(execution_ledger.BudgetExceeded):
            execution_ledger.check_budget(db, run_id, cloud)
    finally:
        db.close()


def test_builtin_cloud_reservation_blocks_agent_and_settled_usage_is_accounted(
    config, store, request_data, cloud
):
    db = database.connect(config.state_root)
    try:
        run_id = bound_run(config, db, store, request_data, cloud)
        execution_ledger.start(config, db, run_id, cloud)
        execution_ledger.reserve(config, db, run_id, cloud)
        model = models.resolve(config, "opencodego-primary")
        op = cloud_intent(config, db, model, "cloud-one")
        reservation = json.loads(op["payload_json"])["cloud_reservation"]
        amount = cloud_budget.charged(reservation, {"prompt_tokens": 100, "completion_tokens": 30})
        agent_store.finish(db, op["id"], "succeeded", {"cloud_cost_nusd": amount})
        assert cloud_budget.totals(db, reservation["billing_day"]) == (1, 130)
        with pytest.raises(InvalidInput, match="agent_cloud_budget_exhausted"):
            cloud_budget.admit(db, model)
    finally:
        db.close()


def test_installed_hermes_cloud_request_has_one_call_and_no_remote_metadata(tmp_path):
    checkout = os.environ.get("FIBER_HERMES_CHECKOUT")
    if not checkout:
        pytest.skip("explicit installed Hermes cloud proxy test")
    from fiber_bundle.agent_connections import hermes_entry

    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    (home / "config.yaml").write_text(
        json.dumps({"agent": {"api_max_retries": 1}, "compression": {"enabled": False}})
    )
    (home / ".env").write_text("")
    key = tmp_path / "synthetic.key"
    key.write_text("synthetic-cloud-key-0000000000000000")
    key.chmod(0o600)
    output = tmp_path / "result.json"
    wrapper = Path(__file__).with_name("cloud_entry_probe.py")
    payload = {
        "root": checkout,
        "home": str(home),
        "output": str(output),
        "model": {
            "processing_location": "cloud",
            "endpoint": "https://opencode.ai/zen/go/v1/chat/completions",
            "model": "synthetic-cloud",
            "secret_file": str(key),
            "max_input_tokens": 16000,
            "max_request_bytes": 65536,
            "timeout_seconds": 30,
        },
        "timeout_seconds": 30,
        "system": "Return a short answer about synthetic coolant.",
        "user": "Synthetic coolant target is 37 C.",
        "max_tokens": 1024,
        "session_id": str(uuid4()),
    }
    result = subprocess.run(
        [str(Path(checkout) / "venv/bin/python"), "-I", "-B", str(wrapper), hermes_entry.__file__],
        input=json.dumps(payload).encode(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=home,
        env={"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8", "HERMES_HOME": str(home)},
        timeout=35,
        check=False,
    )
    assert result.returncode == 0
    value = json.loads(output.read_text())
    assert value["provider_calls"] == 1 and value["usage_state"] == "reported"
    assert "37 C" in value["answer"]
