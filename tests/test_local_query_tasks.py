import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from unittest.mock import patch

import pytest
from test_local_query_gateway import completion, server
from test_query_routing_preparation import routing as routing
from test_session_search import intake
from test_session_search import owner as owner

from fiber_bundle import answer_tasks, answer_worker, auth, database
from fiber_bundle import local_query_ledger as ledger
from fiber_bundle import local_query_tasks as tasks
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.local_query_contract import prepare
from fiber_bundle.local_query_gateway import REASONS, complete
from fiber_bundle.query_routing_config import parse


@pytest.fixture
def route(routing):
    return parse(routing)


@pytest.fixture
def local_task(http, owner, config, route):
    receipt = intake(http, owner)
    db = database.connect(config.state_root)
    data = answer_tasks.AnswerInput(
        receipt["job_id"], "current", "What pressure?", "psi", submission_id="local-one"
    )
    result = tasks.submit(config, db, route, owner[0], data)
    db.close()
    return result["task_id"], data


def body(route, owned):
    return completion(prepare(route.local_query, owned.request))


def new_task(config, db, route, client, data, name):
    return tasks.submit(config, db, route, client, replace(data, submission_id=name))["task_id"]


def test_durable_local_only_answer_has_no_cloud_fee_rows(config, route, owner, local_task):
    tid, _ = local_task
    db = database.connect(config.state_root)
    owned = ledger.claim(config, db, route, owner[0], tid)
    assert "100 psi session evidence" in json.dumps(owned.request)
    assert "RAW-EVENT-SECRET" not in json.dumps(owned.request)
    report = tasks.status(config, db, owner[0], tid)
    assert report["local_call"]["state"] == "reserved"
    assert owned.call_id not in json.dumps(report)
    assert ledger.finish(config, db, route, owned, body(route, owned))["state"] == "succeeded"
    result = tasks.deliver(config, db, owner[0], tid)
    assert result["quotations"][0]["quote"] == "100 psi session evidence"
    assert result["model_used"] is True and result["execution_role"] == "local_query"
    assert result["sources"][0]["originals"] == []
    assert tasks.status(config, db, owner[0], tid)["local_call"]["input_tokens"] == 20
    for table in ("execution_runs", "model_calls", "answer_tasks"):
        assert db.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0
    assert not {"currency", "reserved_nusd", "charged_nusd"} & {
        r[1] for r in db.execute("PRAGMA table_info(local_query_calls)")
    }
    assert answer_worker.dispatch(config, route.cloud_answer)["state"] == "idle"
    db.close()


def test_submission_is_idempotent_and_does_not_replay(config, route, owner, local_task):
    tid, data = local_task
    db = database.connect(config.state_root)
    assert tasks.submit(config, db, route, owner[0], data)["task_id"] == tid
    owned = ledger.claim(config, db, route, owner[0], tid)
    ledger.finish(config, db, route, owned, body(route, owned))
    assert tasks.submit(config, db, route, owner[0], data)["state"] == "succeeded"
    with pytest.raises(InvalidInput, match="not_replayable"):
        ledger.claim(config, db, route, owner[0], tid)
    with pytest.raises(InvalidInput, match="not_active"):
        ledger.finish(config, db, route, owned, body(route, owned))
    assert db.execute("SELECT count(*) FROM local_query_calls").fetchone()[0] == 1
    db.close()


@pytest.mark.parametrize("change", ["question", "query", "mode", "config", "receipt"])
def test_submission_conflicts_are_atomic(config, route, owner, local_task, change):
    _, data = local_task
    if change == "question":
        data = replace(data, question="Different question?")
    elif change == "query":
        data = replace(data, retrieval_query="other")
    elif change == "mode":
        data = replace(data, mode="cited_draft")
    elif change == "receipt":
        data = replace(data, receipt_id="other-receipt")
    else:
        route = replace(route, local_query=replace(route.local_query, max_output_tokens=256))
    db = database.connect(config.state_root)
    with pytest.raises(InvalidInput, match="submission_conflict"):
        tasks.submit(config, db, route, owner[0], data)
    assert db.execute("SELECT count(*) FROM local_query_tasks").fetchone()[0] == 1
    db.close()


@pytest.mark.parametrize("change", ["revoke", "grant", "session", "evidence"])
def test_changed_authority_or_snapshot_prevents_claim_without_counting(
    config, route, owner, local_task, change
):
    tid, _ = local_task
    db = database.connect(config.state_root)
    if change == "revoke":
        auth.revoke_client(db, owner[0])
    elif change == "grant":
        auth.set_grant(db, owner[0], owner[2]["personal"], query=False, original=False, add=True)
    elif change == "session":
        db.execute("DELETE FROM session_refs")
    else:
        (config.project_root / "test vault/temp_vault1/new.md").write_text("pressure 90 psi")
    with patch("subprocess.run", side_effect=AssertionError("provider")):
        with pytest.raises((auth.Denied, IntegrityError)):
            ledger.claim(config, db, route, owner[0], tid)
    assert db.execute("SELECT state FROM local_query_tasks").fetchone()[0] == "queued"
    assert db.execute("SELECT count(*) FROM local_query_calls").fetchone()[0] == 0
    db.close()


@pytest.mark.parametrize("change", ["revoke", "session", "evidence", "quotation"])
def test_inflight_rejection_retains_reported_usage_without_result(
    config, route, owner, local_task, change
):
    tid, _ = local_task
    db = database.connect(config.state_root)
    owned = ledger.claim(config, db, route, owner[0], tid)
    output = body(route, owned)
    if change == "revoke":
        auth.revoke_client(db, owner[0])
    elif change == "session":
        db.execute("DELETE FROM session_refs")
    elif change == "evidence":
        (config.project_root / "test vault/temp_vault1/new.md").write_text("pressure 90 psi")
    else:
        proposal = json.loads(output["choices"][0]["message"]["content"])
        proposal["quotations"][0]["quote"] = "invented 999 psi"
        output["choices"][0]["message"]["content"] = json.dumps(proposal)
    result = ledger.finish(config, db, route, owned, output)
    assert result["state"] == "failed" and result["error_code"] == "local_result_rejected"
    assert tasks.get(db, owner[0], tid)["result_json"] is None
    assert tuple(
        db.execute("SELECT state,input_tokens,output_tokens FROM local_query_calls").fetchone()
    ) == ("settled", 20, 30)
    with pytest.raises((InvalidInput, auth.Denied, IntegrityError)):
        tasks.deliver(config, db, owner[0], tid)
    db.close()


@pytest.mark.parametrize("reason", sorted(REASONS))
def test_failure_preserves_unknown_attempt_and_never_retries(
    config, route, owner, local_task, reason
):
    tid, data = local_task
    db = database.connect(config.state_root)
    owned = ledger.claim(config, db, route, owner[0], tid)
    result = ledger.fail(config, db, route, owned, reason)
    assert result["state"] == "failed" and result["error_code"] == reason
    assert tasks.status(config, db, owner[0], tid)["local_call"]["state"] == "uncertain"
    assert tasks.status(config, db, owner[0], tid)["local_call"]["input_tokens"] is None
    assert tasks.submit(config, db, route, owner[0], data)["state"] == "failed"
    with pytest.raises(InvalidInput, match="not_replayable"):
        ledger.claim(config, db, route, owner[0], tid)
    db.close()


@pytest.mark.parametrize("bad", ["PRIVATE ERROR BODY", None, [], 1])
def test_failure_reason_is_bounded_and_does_not_change_attempt(
    config, route, owner, local_task, bad
):
    db = database.connect(config.state_root)
    owned = ledger.claim(config, db, route, owner[0], local_task[0])
    with pytest.raises(InvalidInput, match="failure_code_invalid"):
        ledger.fail(config, db, route, owned, bad)
    assert tasks.status(config, db, owner[0], local_task[0])["state"] == "running"
    db.close()


@pytest.mark.parametrize("change", ["model", "usage", "unfinished", "tools", "json"])
def test_invalid_local_completion_stops_with_unknown_usage(
    config, route, owner, local_task, change
):
    tid, _ = local_task
    db = database.connect(config.state_root)
    owned = ledger.claim(config, db, route, owner[0], tid)
    output = body(route, owned)
    if change == "model":
        output["model"] = "different-model"
    elif change == "usage":
        output["usage"]["prompt_tokens"] = True
    elif change == "unfinished":
        output["choices"][0]["finish_reason"] = "length"
    elif change == "tools":
        output["choices"][0]["message"]["tool_calls"] = [{"id": "evil"}]
    else:
        output["choices"][0]["message"]["content"] = "PRIVATE invalid text"
    assert ledger.finish(config, db, route, owned, output)["state"] == "failed"
    assert tasks.status(config, db, owner[0], tid)["local_call"]["state"] == "uncertain"
    db.close()


def test_cancelled_inflight_result_is_discarded_but_reported_usage_settles(
    config, route, owner, local_task
):
    tid, _ = local_task
    db = database.connect(config.state_root)
    owned = ledger.claim(config, db, route, owner[0], tid)
    assert ledger.stop(config, db, owner[0], tid)["state"] == "cancelled"
    assert tasks.status(config, db, owner[0], tid)["local_call"]["state"] == "uncertain"
    assert ledger.finish(config, db, route, owned, body(route, owned))["state"] == "cancelled"
    assert tasks.status(config, db, owner[0], tid)["local_call"]["state"] == "settled"
    assert tasks.get(db, owner[0], tid)["result_json"] is None
    with pytest.raises(InvalidInput):
        ledger.finish(config, db, route, owned, body(route, owned))
    db.close()


def test_queued_cancel_consumes_no_call_and_terminal_state_cannot_be_revived(
    config, route, owner, local_task
):
    tid, _ = local_task
    db = database.connect(config.state_root)
    assert ledger.stop(config, db, owner[0], tid, interrupt=True)["state"] == "queued"
    assert ledger.stop(config, db, owner[0], tid)["state"] == "cancelled"
    assert ledger.stop(config, db, owner[0], tid, interrupt=True)["state"] == "cancelled"
    assert db.execute("SELECT count(*) FROM local_query_calls").fetchone()[0] == 0
    with pytest.raises(InvalidInput):
        ledger.claim(config, db, route, owner[0], tid)
    db.close()


def test_explicit_recovery_is_terminal_and_late_completion_does_not_revive(
    config, route, owner, local_task
):
    tid, _ = local_task
    db = database.connect(config.state_root)
    owned = ledger.claim(config, db, route, owner[0], tid)
    assert ledger.stop(config, db, owner[0], tid, interrupt=True)["state"] == "interrupted"
    with pytest.raises(InvalidInput):
        ledger.finish(config, db, route, owned, body(route, owned))
    assert tasks.status(config, db, owner[0], tid)["local_call"]["state"] == "uncertain"
    db.close()


def test_daily_limit_counts_unknown_calls_across_models_and_resets_only_by_utc_day(
    config, route, owner, local_task
):
    _, data = local_task
    db = database.connect(config.state_root)
    route = replace(route, local_query=replace(route.local_query, daily_calls=1))
    first = new_task(config, db, route, owner[0], data, "limit-one")
    with patch.object(ledger, "_day", return_value="2026-10-04"):
        owned = ledger.claim(config, db, route, owner[0], first)
        ledger.fail(config, db, route, owned, "local_timeout")
        changed = replace(route, local_query=replace(route.local_query, model="new-model"))
        second = new_task(config, db, changed, owner[0], data, "limit-two")
        with pytest.raises(ledger.LocalLimitExceeded):
            ledger.claim(config, db, changed, owner[0], second)
        assert tasks.status(config, db, owner[0], second)["state"] == "queued"
    with patch.object(ledger, "_day", return_value="2026-10-05"):
        ledger.claim(config, db, changed, owner[0], second)
    assert db.execute("SELECT count(*) FROM local_query_calls").fetchone()[0] == 2
    db.close()


def test_concurrent_claims_cannot_exceed_one_daily_slot(config, route, owner, local_task):
    route = replace(route, local_query=replace(route.local_query, daily_calls=1))
    db = database.connect(config.state_root)
    ids = [new_task(config, db, route, owner[0], local_task[1], f"parallel-{i}") for i in range(8)]
    db.close()

    def attempt(tid):
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                ledger.claim(config, db, route, owner[0], tid)
                return True
            except ledger.LocalLimitExceeded:
                return False
            finally:
                db.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, ids)) == 1
    db = database.connect(config.state_root)
    assert db.execute("SELECT count(*) FROM local_query_calls").fetchone()[0] == 1
    assert (
        db.execute("SELECT count(*) FROM local_query_tasks WHERE state='running'").fetchone()[0]
        == 1
    )
    db.close()


@pytest.mark.parametrize("prefix", ["INSERT INTO local_query_calls", "UPDATE local_query_tasks"])
def test_claim_fault_rolls_back_ownership_and_daily_counter(
    config, route, owner, local_task, prefix
):
    class Fault(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            result = super().execute(sql, parameters)
            if sql.startswith(prefix):
                raise sqlite3.OperationalError("synthetic claim fault")
            return result

    db = sqlite3.connect(
        config.state_root / "metadata.sqlite3", isolation_level=None, factory=Fault
    )
    db.row_factory = sqlite3.Row
    with pytest.raises(sqlite3.OperationalError):
        ledger.claim(config, db, route, owner[0], local_task[0])
    assert tasks.get(db, owner[0], local_task[0])["state"] == "queued"
    assert db.execute("SELECT count(*) FROM local_query_calls").fetchone()[0] == 0
    db.close()


def test_real_process_exit_retains_reservation_and_forbids_replay(config, route, owner, local_task):
    tid, _ = local_task
    code = """
import json,os,sys
from pathlib import Path
from fiber_bundle import database,local_query_ledger
from fiber_bundle.config import load_config
from fiber_bundle.query_routing_config import parse
v=json.load(sys.stdin);root=Path(v['root']);config=load_config(root/'config.json',root)
with database.locked(config):
 db=database.connect(config.state_root)
 local_query_ledger.claim(config,db,parse(v['policy']),v['client'],v['task'])
 os._exit(23)
"""
    declared = {"schema": "query-routing-preparation/v1", "enabled": False, **asdict(route)}
    declared["approved_backups"] = list(declared["approved_backups"])
    result = subprocess.run(
        [sys.executable, "-c", code],
        input=json.dumps(
            {"root": str(config.project_root), "policy": declared, "client": owner[0], "task": tid}
        ),
        text=True,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 23 and not result.stdout and not result.stderr
    with database.locked(config):
        db = database.connect(config.state_root)
        assert tasks.status(config, db, owner[0], tid)["local_call"]["state"] == "reserved"
        with pytest.raises(InvalidInput):
            ledger.claim(config, db, route, owner[0], tid)
        assert ledger.stop(config, db, owner[0], tid, interrupt=True)["state"] == "interrupted"
        with pytest.raises(InvalidInput):
            ledger.claim(config, db, route, owner[0], tid)
        db.close()


def test_wrong_client_config_and_claim_token_are_rejected(config, route, owner, local_task):
    tid, _ = local_task
    db = database.connect(config.state_root)
    other = auth.create_client(db, "other local client", False)["id"]
    for fn in (tasks.status, tasks.deliver, ledger.stop):
        with pytest.raises(auth.Denied):
            fn(config, db, other, tid)
    with pytest.raises(auth.Denied):
        ledger.claim(config, db, route, other, tid)
    changed = replace(route, local_query=replace(route.local_query, max_output_tokens=256))
    with pytest.raises(InvalidInput, match="configuration_changed"):
        ledger.claim(config, db, changed, owner[0], tid)
    owned = ledger.claim(config, db, route, owner[0], tid)
    for fake in (
        replace(owned, client_id=other),
        replace(owned, call_id="fake"),
        replace(owned, configuration_sha256=changed.digest),
    ):
        with pytest.raises((auth.Denied, InvalidInput)):
            ledger.finish(config, db, route, fake, body(route, owned))
    assert tasks.status(config, db, owner[0], tid)["local_call"]["state"] == "reserved"
    db.close()


def test_unconfigured_local_and_bad_submission_do_not_insert(config, route, owner, local_task):
    db = database.connect(config.state_root)
    with pytest.raises(InvalidInput, match="not_configured"):
        tasks.submit(config, db, replace(route, local_query=None), owner[0], local_task[1])
    for bad in (None, "", "PRIVATE\nID"):
        with pytest.raises(InvalidInput):
            tasks.submit(config, db, route, owner[0], replace(local_task[1], submission_id=bad))
    assert db.execute("SELECT count(*) FROM local_query_tasks").fetchone()[0] == 1
    db.close()


def test_real_http_adapter_roundtrip_uses_durable_claim_before_post(
    config, route, owner, local_task
):
    with server() as (local, requests):
        route = replace(route, local_query=replace(local, secret_file=None))
        db = database.connect(config.state_root)
        tid = new_task(config, db, route, owner[0], local_task[1], "http-local")
        owned = ledger.claim(config, db, route, owner[0], tid)
        db.close()
        # No storage lock is held while the provider runs; cancellation can proceed.
        output = complete(route.local_query, owned.request, allow_inference=True)
        db = database.connect(config.state_root)
        assert ledger.finish(config, db, route, owned, output)["state"] == "succeeded"
        assert tasks.deliver(config, db, owner[0], tid)["quotations"]
        assert len(requests) == 1
        db.close()


def test_note_scope_is_frozen_and_rechecked_without_cloud_configuration(config, store, route):
    db = database.connect(config.state_root)
    client = auth.create_client(db, "note owner", False)["id"]
    vids = {r["name"]: r["id"] for r in db.execute("SELECT * FROM vaults")}
    for vid in vids.values():
        auth.set_grant(db, client, vid, query=True, original=False, add=False)
    for _, relative in config.vaults:
        (config.project_root / relative / "note.md").write_text("synthetic pressure 100 psi")
    db.execute("UPDATE vaults SET default_query=1 WHERE name='personal'")
    route = replace(route, cloud_answer=None)
    data = answer_tasks.ScopedNoteAnswerInput(None, "Pressure?", "psi", submission_id="scope")
    tid = tasks.submit(config, db, route, client, data)["task_id"]
    db.execute("UPDATE vaults SET default_query=1 WHERE name='supplier'")
    assert tasks.submit(config, db, route, client, data)["task_id"] == tid
    owned = ledger.claim(config, db, route, client, tid)
    ledger.finish(config, db, route, owned, body(route, owned))
    result = tasks.deliver(config, db, client, tid)
    assert result["searched_vault_ids"] == [vids["personal"]]
    auth.set_grant(db, client, vids["personal"], query=False, original=False, add=False)
    with pytest.raises(auth.Denied):
        tasks.deliver(config, db, client, tid)
    db.close()


def test_foreign_session_and_partial_scope_cannot_create_tasks(config, route, owner, local_task):
    db = database.connect(config.state_root)
    other = auth.create_client(db, "other", False)["id"]
    auth.set_grant(db, other, owner[2]["personal"], query=True, original=False, add=False)
    with pytest.raises(auth.Denied):
        tasks.submit(config, db, route, other, replace(local_task[1], submission_id="foreign"))
    data = answer_tasks.ScopedNoteAnswerInput(
        (owner[2]["personal"], owner[2]["supplier"]), "Pressure?", "psi", submission_id="partial"
    )
    with pytest.raises(auth.Denied):
        tasks.submit(config, db, route, owner[0], data)
    assert db.execute("SELECT count(*) FROM local_query_tasks").fetchone()[0] == 1
    db.close()


def test_status_has_no_evidence_credentials_or_worker_claim_token(config, route, owner, local_task):
    db = database.connect(config.state_root)
    owned = ledger.claim(config, db, route, owner[0], local_task[0])
    report = json.dumps(tasks.status(config, db, owner[0], local_task[0]))
    for private in (
        owned.call_id,
        local_task[1].question,
        "100 psi session evidence",
        "RAW-EVENT-SECRET",
        route.local_query.secret_file,
        route.local_query.model,
        route.local_query.endpoint,
    ):
        assert private not in report
    db.close()


@pytest.mark.parametrize("moment", ["before", "during"])
def test_physical_vault_replacement_is_rejected(config, route, owner, local_task, moment):
    db = database.connect(config.state_root)
    tid = local_task[0]
    owned = ledger.claim(config, db, route, owner[0], tid) if moment == "during" else None
    root = config.project_root / "test vault/temp_vault1"
    root.rename(root.with_name("old-vault"))
    root.mkdir()
    if moment == "before":
        with pytest.raises((IntegrityError, InvalidInput, auth.Denied)):
            ledger.claim(config, db, route, owner[0], tid)
        assert db.execute("SELECT count(*) FROM local_query_calls").fetchone()[0] == 0
    else:
        assert ledger.finish(config, db, route, owned, body(route, owned))["state"] == "failed"
        assert tasks.get(db, owner[0], tid)["result_json"] is None
        assert db.execute("SELECT state FROM local_query_calls").fetchone()[0] == "settled"
    db.close()


def test_owner_can_cancel_after_query_withdrawal_without_reading_result(
    config, route, owner, local_task
):
    db = database.connect(config.state_root)
    owned = ledger.claim(config, db, route, owner[0], local_task[0])
    auth.set_grant(db, owner[0], owner[2]["personal"], query=False, original=False, add=True)
    assert ledger.stop(config, db, owner[0], local_task[0])["state"] == "cancelled"
    assert ledger.finish(config, db, route, owned, body(route, owned))["state"] == "cancelled"
    with pytest.raises(auth.Denied):
        tasks.deliver(config, db, owner[0], local_task[0])
    db.close()


@pytest.mark.parametrize("change", ["local_dict", "cloud_dict", "backups_list"])
def test_programmatic_role_types_fail_closed(config, route, owner, local_task, change):
    if change == "local_dict":
        route = replace(route, local_query=asdict(route.local_query))
    elif change == "cloud_dict":
        route = replace(route, cloud_answer=asdict(route.cloud_answer))
    else:
        route = replace(route, approved_backups=[route.cloud_answer])
    db = database.connect(config.state_root)
    with pytest.raises(InvalidInput, match="configuration_type_invalid"):
        tasks.submit(config, db, route, owner[0], local_task[1])
    assert db.execute("SELECT count(*) FROM local_query_calls").fetchone()[0] == 0
    db.close()
