import json
from dataclasses import replace
from unittest.mock import patch

import pytest
from test_local_query_tasks import body
from test_local_query_tasks import local_task as local_task
from test_local_query_tasks import route as route
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner

from fiber_bundle import (
    answer_worker,
    auth,
    cloud_answer_binding,
    database,
    execution,
    execution_ledger,
    local_query_ledger,
)
from fiber_bundle import cloud_answer_tasks as tasks
from fiber_bundle.config import IntegrityError, InvalidInput


@pytest.fixture
def cloud_task(config, route, owner, local_task):
    db = database.connect(config.state_root)
    attempt = local_query_ledger.claim(config, db, route, owner[0], local_task[0])
    local_query_ledger.finish(config, db, route, attempt, body(route, attempt))
    run = tasks.submit(config, db, route, owner[0], local_task[0], "edit-one")["run_id"]
    db.close()
    return run


def result(*, quotations=None, **kwargs):
    return {
        "answer": json.dumps(
            {
                "text": "Unverified edited answer [1].",
                "quotations": quotations
                if quotations is not None
                else [{"citation": "[1]", "quote": "100 psi session evidence"}],
            }
        ),
        "usage": {"prompt_tokens": 20, "completion_tokens": 30},
        **kwargs,
    }


def test_durable_edit_binding_never_contains_retrieval_or_raw_evidence(
    config, route, owner, local_task, cloud_task
):
    db = database.connect(config.state_root)
    answer = tasks.current(config, db, route, owner[0], cloud_task)
    value = json.dumps(answer)
    assert set(answer) == {"schema", "answer"}
    assert "100 psi session evidence" in value
    assert "What pressure?" not in value and "RAW-EVENT-SECRET" not in value
    assert not any(
        k in value for k in ["retrieval_query", "originals", "request_json", "snapshot_json"]
    )
    binding = cloud_answer_binding.get(db, cloud_task)
    assert set(binding.keys()) == {
        "run_id",
        "client_id",
        "submission_id",
        "local_task_id",
        "routing_digest",
        "local_result_sha256",
        "result_text",
    }
    assert answer_worker.dispatch(config, route.cloud_answer)["state"] == "idle"
    assert (
        tasks.submit(config, db, route, owner[0], local_task[0], "edit-one")["run_id"] == cloud_task
    )
    attempt = tasks.claim(config, db, route, owner[0], cloud_task)
    assert tasks.status(db, owner[0], cloud_task)["cloud_call"]["state"] == "reserved"
    assert attempt.call_id not in json.dumps(tasks.status(db, owner[0], cloud_task))
    assert tasks.finish(config, db, route, attempt, result())["state"] == "succeeded"
    delivered = tasks.deliver(config, db, owner[0], cloud_task)
    assert delivered["cloud_answer"]["semantic_verified"] is False
    assert delivered["cloud_answer"]["text"] == (
        "Unverified edited answer [1].\n\n[1] 100 psi session evidence"
    )
    assert delivered["quotations"][0]["quote"] == "100 psi session evidence"
    assert "云端整理" in delivered["cautions"][-1]
    assert tuple(db.execute("SELECT state,charged_nusd FROM model_calls").fetchone()) == (
        "settled",
        18000,
    )
    with pytest.raises(InvalidInput, match="not_replayable"):
        tasks.claim(config, db, route, owner[0], cloud_task)
    with pytest.raises(InvalidInput, match="not_active"):
        tasks.finish(config, db, route, attempt, result())
    db.close()


@pytest.mark.parametrize("state", ["queued", "running", "failed", "cancelled", "interrupted"])
def test_local_incomplete_or_failed_never_queues_cloud(config, route, owner, local_task, state):
    db = database.connect(config.state_root)
    db.execute("UPDATE local_query_tasks SET state=?", (state,))
    with patch("subprocess.run", side_effect=AssertionError("network or key")):
        with pytest.raises(InvalidInput, match="not_ready"):
            tasks.submit(config, db, route, owner[0], local_task[0], "no-cloud")
    assert db.execute("SELECT count(*) FROM execution_runs").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    db.close()


@pytest.mark.parametrize("change", ["route", "task", "result", "owner"])
def test_submission_rejects_conflict_or_forged_result_binding(
    config, route, owner, local_task, cloud_task, change
):
    db = database.connect(config.state_root)
    tid = local_task[0]
    who = owner[0]
    if change == "route":
        route = replace(route, cloud_answer=replace(route.cloud_answer, price_revision="different"))
    elif change == "task":
        tid = "different-task"
    elif change == "result":
        db.execute("UPDATE local_query_tasks SET result_json='{}'")
    else:
        who = auth.create_client(db, "foreign", False)["id"]
    with pytest.raises((auth.Denied, InvalidInput, IntegrityError)):
        tasks.submit(config, db, route, who, tid, "edit-one")
    assert db.execute("SELECT count(*) FROM execution_runs").fetchone()[0] == 1
    db.close()


@pytest.mark.parametrize("change", ["revoke", "grant", "session", "evidence", "result", "route"])
def test_preflight_recheck_prevents_any_reservation(config, route, owner, cloud_task, change):
    db = database.connect(config.state_root)
    mutate(config, db, owner, change)
    if change == "route":
        route = replace(route, cloud_answer=replace(route.cloud_answer, price_revision="different"))
    with pytest.raises((auth.Denied, InvalidInput, IntegrityError)):
        tasks.claim(config, db, route, owner[0], cloud_task)
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    assert db.execute("SELECT state FROM execution_runs").fetchone()[0] == "queued"
    db.close()


def mutate(config, db, owner, change):
    if change == "revoke":
        auth.revoke_client(db, owner[0])
    elif change == "grant":
        auth.set_grant(db, owner[0], owner[2]["personal"], query=False, original=False, add=True)
    elif change == "session":
        db.execute("DELETE FROM session_refs")
    elif change == "evidence":
        (config.project_root / "test vault/temp_vault1/new.md").write_text("pressure 90 psi")
    elif change == "result":
        db.execute("UPDATE local_query_tasks SET result_json='{}'")


@pytest.mark.parametrize("change", ["revoke", "grant", "session", "evidence", "result"])
def test_late_authority_rejection_keeps_usage_and_discards_cloud_text(
    config, route, owner, cloud_task, change
):
    db = database.connect(config.state_root)
    owned = tasks.claim(config, db, route, owner[0], cloud_task)
    mutate(config, db, owner, change)
    report = tasks.finish(config, db, route, owned, result())
    assert report["state"] == "failed" and report["error_code"] == "cloud_authority_changed"
    assert db.execute("SELECT state FROM model_calls").fetchone()[0] == "settled"
    assert cloud_answer_binding.get(db, cloud_task)["result_text"] is None
    db.close()


@pytest.mark.parametrize(
    "usage,expected",
    [
        (None, "uncertain"),
        ({"prompt_tokens": True, "completion_tokens": 1}, "uncertain"),
        ({"prompt_tokens": 20, "completion_tokens": 1000}, "settled"),
    ],
)
def test_invalid_or_excess_usage_preserves_conservative_cost(
    config, route, owner, cloud_task, usage, expected
):
    db = database.connect(config.state_root)
    owned = tasks.claim(config, db, route, owner[0], cloud_task)
    assert (
        tasks.finish(config, db, route, owned, result(usage=usage))["error_code"]
        == "cloud_usage_rejected"
    )
    call = db.execute("SELECT * FROM model_calls").fetchone()
    assert call["state"] == expected
    assert cloud_answer_binding.get(db, cloud_task)["result_text"] is None
    if expected == "settled":
        assert call["charged_nusd"] == 503000
    db.close()


@pytest.mark.parametrize("answer", ["", " ", None, "x" * 16385, "\ud800"])
def test_bad_cloud_content_does_not_discard_valid_accounting(
    config, route, owner, cloud_task, answer
):
    db = database.connect(config.state_root)
    owned = tasks.claim(config, db, route, owner[0], cloud_task)
    assert (
        tasks.finish(config, db, route, owned, result(answer=answer))["error_code"]
        == "cloud_response_rejected"
    )
    assert db.execute("SELECT state FROM model_calls").fetchone()[0] == "settled"
    assert cloud_answer_binding.get(db, cloud_task)["result_text"] is None
    db.close()


@pytest.mark.parametrize("interrupt", [False, True])
def test_cancel_or_recover_retains_unknown_call_and_late_usage(
    config, route, owner, cloud_task, interrupt
):
    db = database.connect(config.state_root)
    owned = tasks.claim(config, db, route, owner[0], cloud_task)
    target = "interrupted" if interrupt else "cancelled"
    assert tasks.stop(db, owner[0], cloud_task, interrupt=interrupt)["state"] == target
    assert db.execute("SELECT state FROM model_calls").fetchone()[0] == "uncertain"
    assert tasks.finish(config, db, route, owned, result())["state"] == target
    assert db.execute("SELECT state FROM model_calls").fetchone()[0] == "settled"
    assert cloud_answer_binding.get(db, cloud_task)["result_text"] is None
    with pytest.raises(InvalidInput):
        tasks.claim(config, db, route, owner[0], cloud_task)
    db.close()


def test_old_executor_cannot_send_cloud_bound_task_or_use_vault_tools(
    config, route, owner, cloud_task
):
    db = database.connect(config.state_root)
    assert db.execute("SELECT state FROM execution_runs").fetchone()[0] == "queued"
    db.close()
    with patch("subprocess.Popen", side_effect=AssertionError("executor")):
        with pytest.raises(InvalidInput, match="bound_executor"):
            execution.execute(config, route.cloud_answer, cloud_task, "UNTRUSTED PRIVATE PROMPT")
        with pytest.raises(InvalidInput, match="bound_executor"):
            execution.handle(
                config,
                route.cloud_answer,
                cloud_task,
                {"type": "model", "id": "x", "body": {"messages": []}},
            )


@pytest.mark.parametrize("prior_state", ["reserved", "uncertain", "settled"])
def test_global_fee_budget_includes_preexisting_non_edit_calls(
    config, route, owner, cloud_task, prior_state
):
    db = database.connect(config.state_root)
    source = db.execute("SELECT * FROM local_query_tasks").fetchone()
    auth.set_grant(db, owner[0], source["vault_id"], query=True, original=True, add=True)
    prior = execution_ledger.create_run(
        config, db, route.cloud_answer, owner[0], source["vault_id"], source["version_id"]
    )
    # The generic source executor also requires original permission.
    execution_ledger.start(config, db, prior, route.cloud_answer)
    call = execution_ledger.reserve(config, db, prior, route.cloud_answer)
    db.execute("UPDATE model_calls SET state=? WHERE id=?", (prior_state, call))
    # Use up the remaining daily count with another legacy call.
    db.execute(
        "INSERT INTO model_calls SELECT ?,run_id,model,price_revision,billing_day,currency,"
        "reserved_nusd,charged_nusd,input_tokens,output_tokens,state,created_at "
        "FROM model_calls WHERE id=?",
        ("second", call),
    )
    with pytest.raises(execution_ledger.BudgetExceeded):
        tasks.claim(config, db, route, owner[0], cloud_task)
    assert tasks.status(db, owner[0], cloud_task)["error_code"] == "cloud_budget_exceeded"
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 2
    with pytest.raises(InvalidInput, match="not_replayable"):
        tasks.claim(config, db, route, owner[0], cloud_task)
    db.close()


def test_owned_apis_hide_foreign_task_and_late_delivery_rechecks(config, route, owner, cloud_task):
    db = database.connect(config.state_root)
    foreign = auth.create_client(db, "foreign", False)["id"]
    for fn in [
        lambda: tasks.status(db, foreign, cloud_task),
        lambda: tasks.stop(db, foreign, cloud_task),
        lambda: tasks.deliver(config, db, foreign, cloud_task),
    ]:
        with pytest.raises(auth.Denied):
            fn()
    owned = tasks.claim(config, db, route, owner[0], cloud_task)
    tasks.finish(config, db, route, owned, result())
    mutate(config, db, owner, "grant")
    with pytest.raises(auth.Denied):
        tasks.deliver(config, db, owner[0], cloud_task)
    db.close()


@pytest.mark.parametrize("mode", ["extractive", "cited_draft"])
def test_note_answer_editing_preserves_original_quotes_and_marks_prose_unverified(
    config, route, owner, mode
):
    from fiber_bundle import answer_tasks, local_query_tasks

    quote = "Synthetic note pressure 100 psi."
    (config.project_root / "test vault/temp_vault1/note.md").write_text(quote)
    db = database.connect(config.state_root)
    tid = local_query_tasks.submit(
        config,
        db,
        route,
        owner[0],
        answer_tasks.NoteAnswerInput(
            owner[2]["personal"],
            "PRIVATE NOTE QUESTION",
            "pressure",
            mode=mode,
            submission_id="note-local",
        ),
    )["task_id"]
    owned = local_query_ledger.claim(config, db, route, owner[0], tid)
    proposal = {"mode": mode, "quotations": [{"citation": "[1]", "quote": quote}]}
    if mode == "cited_draft":
        proposal["claims"] = [{"text": "Proposed 100 psi.", "citations": ["[1]"]}]
    completion = {
        "model": route.local_query.model,
        "usage": {"prompt_tokens": 20, "completion_tokens": 30},
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps(proposal)},
            }
        ],
    }
    assert local_query_ledger.finish(config, db, route, owned, completion)["state"] == "succeeded"
    run = tasks.submit(config, db, route, owner[0], tid, "note-edit")["run_id"]
    attempt = tasks.claim(config, db, route, owner[0], run)
    sent = json.loads(attempt.answer["answer"])
    assert sent == proposal and "PRIVATE NOTE QUESTION" not in attempt.answer["answer"]
    assert (
        tasks.finish(config, db, route, attempt, result(quotations=proposal["quotations"]))["state"]
        == "succeeded"
    )
    assert tasks.deliver(config, db, owner[0], run)["quotations"] == proposal["quotations"]
    (config.project_root / "test vault/temp_vault1/note.md").write_text("Edited pressure 90 psi.")
    with pytest.raises(IntegrityError):
        tasks.deliver(config, db, owner[0], run)
    db.close()


def test_task_cost_budget_blocks_before_cloud_when_live_limit_is_lower(
    config, route, owner, cloud_task
):
    db = database.connect(config.state_root)
    db.execute("UPDATE execution_runs SET task_limit_nusd=1 WHERE id=?", (cloud_task,))
    with pytest.raises(execution_ledger.BudgetExceeded):
        tasks.claim(config, db, route, owner[0], cloud_task)
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    db.close()


def test_forged_attempt_and_unknown_error_code_do_not_rewrite_ledger(
    config, route, owner, cloud_task
):
    db = database.connect(config.state_root)
    owned = tasks.claim(config, db, route, owner[0], cloud_task)
    for fake in [replace(owned, call_id="other"), replace(owned, routing_digest="other")]:
        with pytest.raises(InvalidInput):
            tasks.finish(config, db, route, fake, result())
    with pytest.raises(InvalidInput, match="failure_code"):
        tasks.fail(db, route, owned, "SECRET provider message")
    assert db.execute("SELECT state FROM model_calls").fetchone()[0] == "reserved"
    db.close()
