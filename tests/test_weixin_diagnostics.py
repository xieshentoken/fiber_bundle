"""Read-only fault location, fee preservation, redaction and account scope."""

import json
import sqlite3
from dataclasses import replace
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_execution import policy as policy
from test_weixin_inbox import binding as binding
from test_weixin_inbox import rows
from test_weixin_intents import route
from test_weixin_media_dispatch import frozen as frozen
from test_weixin_publication import articles, complete
from test_weixin_task_dispatch import queued
from test_weixin_worker import cli, startup_files

from fiber_bundle import auth, database, execution, model_gateway
from fiber_bundle import execution_ledger as ledger
from fiber_bundle import weixin_batches as batches
from fiber_bundle import weixin_confirmations as confirmations
from fiber_bundle import weixin_diagnostics as diagnostics
from fiber_bundle import weixin_inbox as inbox
from fiber_bundle import weixin_media_queue as media
from fiber_bundle import weixin_publication as publication
from fiber_bundle import weixin_task_delivery as delivery
from fiber_bundle import weixin_task_dispatch as dispatch
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.weixin_worker_config import WorkerConfig


def dump(config):
    result = {}
    for name in ("metadata.sqlite3", "weixin-inbox.sqlite3"):
        with sqlite3.connect(config.state_root / name) as db:
            result[name] = list(db.iterdump())
    return result


def inspect(config, binding, bid):
    before = dump(config)
    with (
        patch.object(execution, "execute") as execute,
        patch.object(model_gateway, "complete") as provider,
    ):
        result = diagnostics.inspect(config, binding, batch_id=bid)
    assert dump(config) == before
    execute.assert_not_called()
    provider.assert_not_called()
    text = json.dumps(result)
    for secret in ("synthetic-private-route", "private diagnostic failure", "合成正文"):
        assert secret not in text
    assert result["evidence_checked"] is False
    return result


def test_empty_initialized_journal_does_not_create_tables(config, binding):
    before = dump(config)
    assert diagnostics.inspect(config, binding)["batches"] == []
    assert before == dump(config)


def test_missing_main_run_identifies_fixed_submit_without_creating_it(config, binding, policy):
    with (
        patch.object(ledger, "create_run", side_effect=OSError("private diagnostic failure")),
        pytest.raises(OSError),
    ):
        queued(config, binding, policy)
    bid = rows(config, "wx_batches")[0]["id"]
    task = inspect(config, binding, bid)["batches"][0]["tasks"]["items"][0]
    assert task["state"] == "missing"
    assert task["actions"] == ["repeat_original_submit_same_policy"]
    assert task["fees"]["calls"] == 0


def test_wrong_account_never_exposes_blocker_or_batches(config, binding, policy):
    bid, runs = articles(config, binding, policy)
    complete(config, policy, runs[0])
    delivery.prepare(config, binding, bid)
    with inbox.connection(config, binding) as db:
        db.execute("UPDATE wx_task_deliveries SET state='unknown'")
    other = replace(binding, account_id="synthetic-other-bot", owner_id="synthetic-other-owner")
    inbox.initialize(config, other)
    result = diagnostics.inspect(config, other)
    assert result["state"] == "inspected" and result["batches"] == []
    assert result["outbound_blockers"] == []


def test_blocker_truncation_retains_account_total(config, binding):
    confirmations.initialize(config, binding)
    with inbox.connection(config, binding) as db:
        for i in range(101):
            bid = str(uuid4())
            db.execute(
                "INSERT INTO wx_batches VALUES(?,?,'frozen',3,0,NULL,NULL)",
                (bid, binding.account_id),
            )
            db.execute(
                "INSERT INTO wx_confirmation_prompts "
                "VALUES(?,?,?,'[]','private diagnostic failure','unknown')",
                (f"{i:016x}", binding.account_id, bid),
            )
    result = diagnostics.inspect(config, binding, limit=1)
    assert len(result["batches"]) == 1 and result["next_cursor"] is not None
    blocker = result["outbound_blockers"][0]
    assert blocker["count"] == 101 and len(blocker["items"]) == 100 and blocker["truncated"]
    assert "private diagnostic failure" not in json.dumps(result)


@pytest.mark.parametrize("after,limit", [(True, 20), (-1, 20), (0, 0), (0, 101), (0, True)])
def test_invalid_pages(config, binding, after, limit):
    with pytest.raises(InvalidInput):
        diagnostics.inspect(config, binding, after=after, limit=limit)


def test_missing_parts_and_failed_media_are_locatable_without_urls(config, binding, frozen):
    job, bid = frozen
    media.initialize(config, binding)
    attempt = media.enqueue(config, binding, job)[0]
    media.claim(config, binding, attempt_id=attempt["id"])
    media.finish(config, binding, attempt["id"], "failed", "download_failed")
    with inbox.connection(config, binding) as db:
        db.execute("UPDATE wx_download_attempts SET error_code='private diagnostic failure'")
    view = inspect(config, binding, bid)["batches"][0]
    assert view["incomplete_count"] == 1
    failed = next(a for a in view["media_attempts"] if a["id"] == attempt["id"])
    assert failed["action"] == "inspect_failure_then_explicit_retry"
    assert "full_url" not in json.dumps(view)


def test_partial_submission_and_claim_do_not_create_missing_run(config, binding, policy):
    from test_weixin_task_inputs import CAPTURE, saved

    from fiber_bundle import weixin_task_bindings as bindings

    bid, handoff = saved(config, binding, "save_separately", ["https://x.com/a/status/1"] * 2)
    run = bindings.submit(
        config,
        binding,
        bid,
        policy,
        capture_policy=CAPTURE,
        parent_receipt_id=handoff["receipts"][0]["job_id"],
    )["run_id"]
    view = inspect(config, binding, bid)["batches"][0]
    assert view["expected_tasks"] == 2 and view["tasks"]["count"] == 1
    assert "review_fixed_submission_scope_and_policy" in view["actions"]
    # Simulate interruption before effects: claim must remain blocked, even on inspection.
    with (
        patch.object(execution, "execute", side_effect=KeyboardInterrupt),
        pytest.raises(KeyboardInterrupt),
    ):
        dispatch.dispatch(config, binding, policy, maximum=1)
    task = inspect(config, binding, bid)["batches"][0]["tasks"]["items"][0]
    assert task["run_id"] == run and task["dispatch_phase"] == "claimed"
    assert task["actions"] == ["verify_executor_stopped_then_worker_reconcile"]


@pytest.mark.parametrize("state", ["waiting_budget", "interrupted"])
def test_conservative_fee_summary_keeps_uncertain_reservation(config, binding, policy, state):
    bid, rid = queued(config, binding, policy)
    db = database.connect(config.state_root)
    ledger.start(config, db, rid, policy)
    ledger.reserve(config, db, rid, policy)
    ledger.interrupt(db, rid)
    ledger.transition(db, rid, state)
    db.execute(
        "UPDATE execution_runs SET error_code='private diagnostic failure' WHERE id=?", (rid,)
    )
    db.close()
    task = inspect(config, binding, bid)["batches"][0]["tasks"]["items"][0]
    assert task["state"] == state and task["fees"]["calls"] == 1
    assert task["fees"]["accounted_nusd"] == policy.reserve_nusd
    assert task["fees"]["charged_nusd"] == 0
    assert task["fees"]["unsettled_reserved_nusd"] == policy.reserve_nusd
    assert "verify_provider_usage_keep_reservation" in task["actions"]


def test_publication_claim_inspect_does_not_repair_or_touch_notes(config, binding, policy):
    bid, runs = articles(config, binding, policy)
    publication.plan(config, binding, bid)
    complete(config, policy, runs[0])
    with inbox.connection(config, binding) as db:
        db.execute("UPDATE wx_task_publications SET phase='claimed'")
    before = {
        p: p.read_bytes() for p in (config.project_root / "test vault").rglob("*") if p.is_file()
    }
    task = inspect(config, binding, bid)["batches"][0]["tasks"]["items"][0]
    assert task["publication_phase"] == "claimed"
    assert "verify_files_then_publication_reconcile" in task["actions"]
    assert before == {p: p.read_bytes() for p in before}


@pytest.mark.parametrize("state", ["unknown", "sending"])
def test_account_send_blocker_visible_outside_batch_page(config, binding, policy, state):
    first, runs = articles(config, binding, policy)
    complete(config, policy, runs[0])
    delivery.prepare(config, binding, first)
    second = route(config, binding, ["新合成资料"], start=10)
    with inbox.connection(config, binding) as db:
        db.execute("UPDATE wx_task_deliveries SET state=?", (state,))
    result = inspect(config, binding, second)
    assert result["state"] == "blocked"
    assert result["outbound_blockers"][0]["items"] == [{"id": first, "state": state}]
    assert result["outbound_blockers"][0]["action"] == "manual_channel_check_keep_outbound_disabled"


def test_cancel_diagnostics_do_not_suggest_replay(config, binding, policy):
    bid, _ = queued(config, binding, policy)
    batches.choose(config, binding, bid, "cancel")
    result = inspect(config, binding, bid)["batches"][0]
    assert result["actions"] == ["keep_history_do_not_resume_cancelled_batch"]
    assert result["tasks"]["items"][0]["state"] == "queued"  # No implicit synchronization.


def test_scope_and_current_authorization(config, binding, policy):
    bid, rid = queued(config, binding, policy)
    with pytest.raises(InvalidInput):
        diagnostics.inspect(config, replace(binding, owner_id="forged"))
    db = database.connect(config.state_root)
    db.execute("UPDATE execution_runs SET config_digest='mismatch' WHERE id=?", (rid,))
    db.close()
    with pytest.raises(IntegrityError):
        diagnostics.inspect(config, binding, batch_id=bid)
    db = database.connect(config.state_root)
    auth.set_grant(db, binding.client_id, binding.vault_id, add=False, query=False, original=False)
    db.close()
    with pytest.raises(auth.Denied):
        diagnostics.inspect(config, binding)


def test_pagination_and_cli_no_policy_secret_or_effects(config, binding, policy):
    first, _ = articles(config, binding, policy)
    second = route(config, binding, ["新合成资料"], start=10)
    one = diagnostics.inspect(config, binding, limit=1)
    assert one["batches"][0]["batch_id"] == first and one["next_cursor"] is not None
    assert (
        diagnostics.inspect(config, binding, after=one["next_cursor"])["batches"][0]["batch_id"]
        == second
    )
    path = startup_files(config, WorkerConfig(binding), policy)
    (path.parent / "configs/execution.example.json").unlink()
    before = dump(config)
    result = cli(config, "inspect", "--batch-id", first)
    assert result.returncode == 0 and json.loads(result.stdout)["state"] == "inspected"
    assert dump(config) == before
    assert cli(config, "inspect", "--limit", "0").returncode == 1
    assert cli(config, "run", "--limit", "1").returncode == 2
