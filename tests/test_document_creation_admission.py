from dataclasses import replace
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from test_document_creation_config import creation_policy as creation_policy
from test_document_creation_tasks import amend, cancel, submit
from test_document_creation_tasks import input_task as input_task
from test_document_creation_tasks import working as working
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner

from fiber_bundle import auth, execution_ledger, local_query_tasks
from fiber_bundle import document_creation_admission as admission
from fiber_bundle import document_creation_ledger as ledger
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.document_creation_config import parse
from fiber_bundle.inputs import canonical
from fiber_bundle.query_routing_config import parse as parse_routing


def preview(config, db, cid, task, policy, **overrides):
    expected = {
        "expected_revision": task["revision"],
        "expected_plan_sha256": task["plan_sha256"],
        "expected_configuration_sha256": policy.sha256,
        **overrides,
    }
    return admission.preview(config, db, cid, task["task_id"], policy, **expected)


def wire(config):
    return {
        name: (config.state_root / name).read_bytes()
        for name in ("metadata.sqlite3", "document-creation.sqlite3")
    }


def call(config, db, owner, input_task, policy, *, state="reserved", charged=None, day=None):
    version = db.execute(
        "SELECT version_id FROM local_query_tasks WHERE id=?", (input_task[0],)
    ).fetchone()[0]
    run_id = execution_ledger.create_run(
        config, db, policy, owner[0], owner[2]["personal"], version, answer=input_task[1]
    )
    execution_ledger.start(config, db, run_id, policy)
    call_id = execution_ledger.reserve(config, db, run_id, policy)
    db.execute(
        "UPDATE model_calls SET state=?,charged_nusd=?,billing_day=coalesce(?,billing_day) "
        "WHERE id=?",
        (state, charged, day, call_id),
    )
    return call_id


def test_preview_binds_current_head_without_writes_or_side_effects(
    config, owner, working, creation_policy
):
    db, tid = working
    task = submit(config, db, owner[0], tid)
    policy = parse(creation_policy)
    before, dump = wire(config), list(db.iterdump())
    with (
        patch("fiber_bundle.execution_config.read_secret", side_effect=AssertionError("secret")),
        patch("fiber_bundle.model_gateway.complete", side_effect=AssertionError("model")),
        patch("socket.create_connection", side_effect=AssertionError("network")),
        patch("subprocess.run", side_effect=AssertionError("process")),
    ):
        first = preview(config, db, owner[0], task, policy)
        assert preview(config, db, owner[0], task, policy) == first
    assert wire(config) == before and list(db.iterdump()) == dump
    assert first["plan_sha256"] == task["plan_sha256"]
    assert first["input_sha256"] == task["input_sha256"]
    assert first["configuration_sha256"] == policy.sha256
    assert first["budget"]["daily_accounted_calls"] == 0
    assert first["budget"]["next_call_reserve_nusd"] == policy.policy().reserve_nusd
    assert first["budget"]["budget_reserved"] is False
    for flag in (
        "capability_verified",
        "runtime_verified",
        "execution_allowed",
        "delivery_allowed",
        "save_allowed",
        "credentials_read",
        "automatic_switch",
    ):
        assert first[flag] is False
    assert first["model_calls"] == 0
    for secret in (
        "100 psi",
        "制作汇报",
        policy.policy().secret_file,
        policy.policy().model,
        policy.policy().endpoint,
    ):
        assert secret not in canonical(first)


@pytest.mark.parametrize("change", ["revision", "digest", "configuration", "bool"])
def test_explicit_binding_mismatch_is_readonly(config, owner, working, creation_policy, change):
    db, tid = working
    task = submit(config, db, owner[0], tid)
    before = wire(config)
    overrides = {
        "revision": {"expected_revision": 2},
        "digest": {"expected_plan_sha256": "0" * 64},
        "configuration": {"expected_configuration_sha256": "0" * 64},
        "bool": {"expected_revision": True},
    }[change]
    with pytest.raises(InvalidInput):
        preview(config, db, owner[0], task, parse(creation_policy), **overrides)
    assert wire(config) == before


def test_amend_invalidates_old_preview_and_keeps_fixed_input(
    config, owner, working, creation_policy
):
    db, tid = working
    first = submit(config, db, owner[0], tid)
    policy = parse(creation_policy)
    old = preview(config, db, owner[0], first, policy)
    second = amend(config, db, owner[0], first)
    with pytest.raises(InvalidInput, match="plan_conflict"):
        preview(config, db, owner[0], first, policy)
    new = preview(config, db, owner[0], second, policy)
    assert new["preview_sha256"] != old["preview_sha256"]
    assert new["input_sha256"] == old["input_sha256"]


@pytest.mark.parametrize("change", ["cancel", "grant", "client", "source", "other", "input"])
def test_current_state_and_authorization_cannot_be_bypassed(
    config, owner, working, creation_policy, change
):
    db, tid = working
    task = submit(config, db, owner[0], tid)
    cid = owner[0]
    if change == "cancel":
        cancel(config, db, cid, task)
    elif change == "grant":
        auth.set_grant(db, cid, owner[2]["personal"], query=False, original=False, add=True)
    elif change == "client":
        auth.revoke_client(db, cid)
    elif change == "other":
        cid = auth.create_client(db, "other", False)["id"]
    elif change == "source":
        (config.project_root / "test vault/temp_vault1/new.md").write_text("pressure 90 psi")
    else:
        db.execute("UPDATE local_query_tasks SET state='cancelled' WHERE id=?", (tid,))
    before = wire(config)
    with pytest.raises((auth.Denied, IntegrityError, InvalidInput)):
        preview(config, db, cid, task, parse(creation_policy))
    assert wire(config) == before


def test_unsupported_format_or_incomplete_config_never_falls_back(
    config, owner, working, creation_policy
):
    db, tid = working
    task = submit(config, db, owner[0], tid)
    creation_policy["formats"] = ["docx"]
    with pytest.raises(InvalidInput, match="format_not_declared"):
        preview(config, db, owner[0], task, parse(creation_policy))
    creation_policy.update(model_config=None, formats=[], capability_revision=None)
    with pytest.raises(InvalidInput, match="not_configured"):
        preview(config, db, owner[0], task, parse(creation_policy))


@pytest.mark.parametrize(
    "state,charged", [("reserved", None), ("uncertain", None), ("settled", 99)]
)
def test_daily_pool_includes_existing_model_calls_and_unknown_fees(
    config, owner, working, input_task, creation_policy, state, charged
):
    db, tid = working
    task = submit(config, db, owner[0], tid)
    policy = parse(creation_policy)
    call(config, db, owner, input_task, policy.policy(), state=state, charged=charged)
    before = wire(config)
    report = preview(config, db, owner[0], task, policy)
    assert report["budget"]["daily_accounted_calls"] == 1
    assert report["budget"]["daily_accounted_nusd"] == (
        policy.policy().reserve_nusd if charged is None else charged
    )
    assert wire(config) == before


@pytest.mark.parametrize("limit", ["calls", "cost"])
def test_budget_exhaustion_after_preview_is_rechecked_without_reserving(
    config, owner, working, input_task, creation_policy, limit
):
    db, tid = working
    if limit == "calls":
        creation_policy["model_config"].update(daily_calls=1)
    else:
        creation_policy["model_config"].update(task_limit_nusd=500000, daily_limit_nusd=500000)
    policy = parse(creation_policy)
    task = submit(config, db, owner[0], tid)
    assert preview(config, db, owner[0], task, policy)["budget"]["budget_reserved"] is False
    call(config, db, owner, input_task, policy.policy())
    before = wire(config)
    with pytest.raises(execution_ledger.BudgetExceeded, match="daily_budget_exhausted"):
        preview(config, db, owner[0], task, policy)
    assert wire(config) == before


def test_billing_day_uses_configured_zone_and_excludes_older_day(
    config, owner, working, input_task, creation_policy
):
    db, tid = working
    creation_policy["model_config"].update(timezone="Asia/Taipei")
    policy = parse(creation_policy)
    task = submit(config, db, owner[0], tid)
    call(config, db, owner, input_task, policy.policy(), day="2000-01-01")
    report = preview(config, db, owner[0], task, policy)
    assert (
        report["budget"]["billing_day"] == datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
    )
    assert report["budget"]["daily_accounted_calls"] == 0


def test_missing_or_corrupt_task_is_not_reconstructed(config, owner, working, creation_policy):
    db, tid = working
    missing = {"task_id": "missing", "revision": 1, "plan_sha256": "0" * 64}
    with pytest.raises(auth.Denied):
        preview(config, db, owner[0], missing, parse(creation_policy))
    assert not (config.state_root / "document-creation.sqlite3").exists()
    task = submit(config, db, owner[0], tid)
    with ledger.connection(config, write=True) as sidecar:
        sidecar.execute("UPDATE tasks SET head_sha256=?", ("0" * 64,))
    before = wire(config)
    with pytest.raises(IntegrityError):
        preview(config, db, owner[0], task, parse(creation_policy))
    assert wire(config) == before


def test_empty_evidence_is_not_admitted(
    config, owner, working, input_task, creation_policy, routing
):
    db, _ = working
    empty = local_query_tasks.submit(
        config,
        db,
        parse_routing(routing),
        owner[0],
        replace(input_task[1], retrieval_query="no-such-evidence", submission_id="empty-input"),
    )
    task = submit(config, db, owner[0], empty["task_id"])
    before = wire(config)
    with pytest.raises(InvalidInput, match="input_insufficient"):
        preview(config, db, owner[0], task, parse(creation_policy))
    assert wire(config) == before


def test_incomplete_settlement_does_not_masquerade_as_budget_available(
    config, owner, working, input_task, creation_policy
):
    db, tid = working
    policy = parse(creation_policy)
    task = submit(config, db, owner[0], tid)
    call(config, db, owner, input_task, policy.policy(), state="settled")
    before = wire(config)
    with pytest.raises(IntegrityError, match="budget_ledger_invalid"):
        preview(config, db, owner[0], task, policy)
    assert wire(config) == before


def test_exact_remaining_cost_is_eligible_but_not_reserved(
    config, owner, working, input_task, creation_policy
):
    db, tid = working
    reserve = parse(creation_policy).policy().reserve_nusd
    creation_policy["model_config"].update(
        task_limit_nusd=reserve, daily_limit_nusd=reserve * 2, daily_calls=2
    )
    policy = parse(creation_policy)
    task = submit(config, db, owner[0], tid)
    call(config, db, owner, input_task, policy.policy())
    before = wire(config)
    result = preview(config, db, owner[0], task, policy)
    assert result["budget"]["daily_accounted_nusd"] + reserve == reserve * 2
    assert result["budget"]["budget_reserved"] is False
    assert wire(config) == before


def test_previews_do_not_allocate_task_or_daily_allowance(config, owner, working, creation_policy):
    db, tid = working
    first = submit(config, db, owner[0], tid, name="one")
    second = submit(config, db, owner[0], tid, name="two")
    creation_policy["model_config"]["daily_calls"] = 1
    policy = parse(creation_policy)
    before = wire(config)
    for task in (first, second):
        result = preview(config, db, owner[0], task, policy)
        assert result["budget"]["daily_budget_available_at_check"] is True
        assert result["execution_allowed"] is result["budget"]["budget_reserved"] is False
    assert wire(config) == before
