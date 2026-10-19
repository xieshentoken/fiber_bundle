import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

import pytest
from test_document_creation_config import creation_policy as creation_policy
from test_document_creation_execution import claim
from test_document_creation_execution import prepared_task as prepared_task
from test_document_creation_output import write_output
from test_document_creation_tasks import input_task as input_task
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner

from fiber_bundle import auth, database, execution_ledger
from fiber_bundle import document_creation_artifacts as artifacts
from fiber_bundle import document_creation_binding as binding
from fiber_bundle import document_creation_dialogue as dialogue
from fiber_bundle import document_creation_execution as execution
from fiber_bundle import document_creation_output as output
from fiber_bundle import document_creation_profile as profile
from fiber_bundle import document_creation_rounds as rounds
from fiber_bundle import document_creation_runner as runner
from fiber_bundle import document_creation_runtime_state as runtime
from fiber_bundle import document_creation_tasks as tasks
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.document_creation_config import parse
from fiber_bundle.inputs import canonical
from fiber_bundle.objects import digest


@pytest.fixture
def attached(config, owner, prepared_task, creation_policy, request):
    creation_policy["model_config"].update(
        image=profile.IMAGE,
        max_calls=2,
        daily_calls=8,
        max_input_tokens=131072,
        max_request_bytes=131072,
        task_limit_nusd=100000000,
        daily_limit_nusd=200000000,
    )
    creation_policy["capability_revision"] = profile.REVISION
    limits = getattr(request, "param", {})
    creation_policy["model_config"].update(limits)
    if limits.get("max_calls") == 1:
        creation_policy["model_config"]["task_limit_nusd"] = 100000000
    if limits.get("task_limit_nusd") == "single-reserve":
        model = creation_policy["model_config"]
        model["task_limit_nusd"] = (
            model["max_input_tokens"] * model["input_nusd"]
            + model["max_output_tokens"] * model["output_nusd"]
        )
    policy = parse(creation_policy)
    with execution.responsibility(config, owner[0], prepared_task["task_id"]) as guard:
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                attempt = claim(config, db, owner[0], prepared_task, policy, guard)
                rounds.attach(config, db, attempt, policy, guard)
                yield db, policy, guard, attempt
            finally:
                db.close()


def first(db, attempt, policy):
    bound = binding.get(db, attempt.task_id)
    return {
        "model": policy.policy().model,
        "messages": [
            {"role": "system", "content": "Synthetic fixed Pi system"},
            {"role": "user", "content": dialogue.prompt(bound["request_json"])},
        ],
        "tools": [dialogue.TOOL],
    }


def body(policy, ordinal=1):
    message = {"role": "assistant", "content": "Synthetic completion"}
    if ordinal == 1:
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "synthetic-draft-call",
                    "type": "function",
                    "function": {
                        "name": "fiber_write_draft",
                        "arguments": json.dumps({"draft": "# Draft\n100 psi [source-1]"}),
                    },
                }
            ],
        }
    return {
        "id": "synthetic",
        "created": 0,
        "model": policy.policy().model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if ordinal == 1 else "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    }


def second(payload, response, fmt="md"):
    call_id, content, assistant = dialogue.draft(response)
    return {
        **payload,
        "messages": [
            *payload["messages"],
            assistant,
            {
                "role": "tool",
                "content": canonical(
                    {
                        "status": "unverified_draft_written",
                        "filename": "draft." + fmt,
                        "byte_count": len(content),
                    }
                ),
                "tool_call_id": call_id,
            },
        ],
    }


def finish_rounds(config, attached):
    db, policy, guard, attempt = attached
    call, payload = rounds.dispatch(config, db, attempt, policy, guard, first(db, attempt, policy))
    reply = rounds.accept(config, db, call, policy, guard, body(policy))
    next_call, _ = rounds.dispatch(config, db, attempt, policy, guard, second(payload, reply))
    rounds.accept(config, db, next_call, policy, guard, body(policy, 2))
    return payload, reply, next_call


def output_file(config, attached, root):
    db, policy, _, attempt = attached
    plan = tasks.plan(config, db, attempt.client_id, attempt.task_id)
    bound = binding.get(db, attempt.task_id)
    context = output.context(plan, attempt.run_id, policy.sha256, bound["request_sha256"])
    raw = dialogue.draft(body(policy))[1]
    write_output(root, plan, context, raw)
    return raw


def test_two_actual_rounds_bind_first_reservation_and_atomically_reserve_next(config, attached):
    db, policy, guard, attempt = attached
    with (
        patch("fiber_bundle.model_gateway.complete", side_effect=AssertionError("provider")),
        patch(
            "fiber_bundle.execution_config.read_secret", side_effect=AssertionError("credential")
        ),
    ):
        call, payload = rounds.dispatch(
            config, db, attempt, policy, guard, first(db, attempt, policy)
        )
        assert call == attempt
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 1
        before = list(db.iterdump())
        with pytest.raises(InvalidInput, match="not_replayable"):
            rounds.dispatch(config, db, attempt, policy, guard, first(db, attempt, policy))
        assert list(db.iterdump()) == before
        reply = rounds.accept(config, db, call, policy, guard, body(policy))
        assert rounds.accept(config, db, call, policy, guard, body(policy)) == reply
        next_call, _ = rounds.dispatch(config, db, attempt, policy, guard, second(payload, reply))
        assert next_call.call_id != attempt.call_id
        assert len(runtime.rows(db, attempt.run_id)) == 2
        rounds.accept(config, db, next_call, policy, guard, body(policy, 2))
        result = execution.status(db, attempt.client_id, attempt.task_id)
        assert result["runtime"]["mode"] == "synthetic"
        assert sum(c["charged_nusd"] for c in result["model_calls"]) == 2 * (10 * 150 + 20 * 500)
        assert result["state"] == "running" and result["runtime_verified"] is False
        assert [r["request_sha256"] for r in runtime.rows(db, attempt.run_id)] == [
            digest(r["request_json"].encode()) for r in runtime.rows(db, attempt.run_id)
        ]
        with pytest.raises(InvalidInput, match="round_limit"):
            rounds.dispatch(config, db, attempt, policy, guard, second(payload, reply))


@pytest.mark.parametrize("mutation", ["user", "tools", "extra_message", "system_fields", "limit"])
def test_first_actual_request_rejected_before_dispatch_or_new_fees(config, attached, mutation):
    db, policy, guard, attempt = attached
    payload = first(db, attempt, policy)
    if mutation == "user":
        payload["messages"][1]["content"] = "different input"
    elif mutation == "tools":
        payload["tools"] = []
    elif mutation == "extra_message":
        payload["messages"].append({"role": "user", "content": "expand source"})
    elif mutation == "system_fields":
        payload["messages"][0]["endpoint"] = "arbitrary"
    else:
        payload["messages"][0]["content"] = "X" * policy.policy().max_request_bytes
    before = list(db.iterdump())
    with pytest.raises(InvalidInput):
        rounds.dispatch(config, db, attempt, policy, guard, payload)
    assert list(db.iterdump()) == before


@pytest.mark.parametrize("mutation", ["prefix", "assistant", "tool_id", "tool_result", "extra"])
def test_followup_request_must_match_exact_prior_response_and_fixed_tool_result(
    config, attached, mutation
):
    db, policy, guard, attempt = attached
    call, payload = rounds.dispatch(config, db, attempt, policy, guard, first(db, attempt, policy))
    reply = rounds.accept(config, db, call, policy, guard, body(policy))
    following = second(payload, reply)
    if mutation == "prefix":
        following["messages"][1]["content"] = "changed source"
    elif mutation == "assistant":
        following["messages"][2]["tool_calls"][0]["function"]["arguments"] = '{"draft":"changed"}'
    elif mutation == "tool_id":
        following["messages"][3]["tool_call_id"] = "other"
    elif mutation == "tool_result":
        following["messages"][3]["content"] = "{}"
    else:
        following["messages"].append({"role": "user", "content": "unapproved addition"})
    before = list(db.iterdump())
    with pytest.raises(InvalidInput):
        rounds.dispatch(config, db, attempt, policy, guard, following)
    assert list(db.iterdump()) == before


@pytest.mark.parametrize(
    "attached", [{"max_calls": 1}, {"task_limit_nusd": "single-reserve"}], indirect=True
)
def test_followup_checks_current_task_call_and_money_limit(config, attached):
    db, policy, guard, attempt = attached
    call, payload = rounds.dispatch(config, db, attempt, policy, guard, first(db, attempt, policy))
    reply = rounds.accept(config, db, call, policy, guard, body(policy))
    before = list(db.iterdump())
    with pytest.raises(execution_ledger.BudgetExceeded):
        rounds.dispatch(config, db, attempt, policy, guard, second(payload, reply))
    assert list(db.iterdump()) == before


@pytest.mark.parametrize("limit", ["daily_calls", "daily_money"])
def test_followup_competes_with_global_daily_pool_without_changing_task_binding(
    config, attached, limit
):
    db, policy, guard, attempt = attached
    call, payload = rounds.dispatch(config, db, attempt, policy, guard, first(db, attempt, policy))
    reply = rounds.accept(config, db, call, policy, guard, body(policy))
    day = db.execute("SELECT billing_day FROM model_calls WHERE id=?", (call.call_id,)).fetchone()[
        0
    ]
    vault_id = db.execute(
        "SELECT vault_id FROM execution_runs WHERE id=?", (attempt.run_id,)
    ).fetchone()[0]
    db.execute(
        "INSERT INTO execution_runs (id,client_id,vault_id,version_id,config_digest,state,"
        "attempts,task_limit_nusd,currency,created_at,updated_at,binding_kind) "
        "VALUES('other-run',?,?,NULL,?,'interrupted',1,?,'USD',1,1,'notes')",
        (attempt.client_id, vault_id, policy.policy().digest, policy.policy().task_limit_nusd),
    )
    count = policy.policy().daily_calls - 1 if limit == "daily_calls" else 1
    reserve = 15000 if limit == "daily_calls" else policy.policy().daily_limit_nusd
    for i in range(count):
        db.execute(
            "INSERT INTO model_calls VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,'uncertain',?)",
            (
                "competitor-" + str(i),
                "other-run",
                policy.policy().model,
                policy.policy().price_revision,
                day,
                "USD",
                reserve,
                1,
            ),
        )
    before = list(db.iterdump())
    with pytest.raises(execution_ledger.BudgetExceeded):
        rounds.dispatch(config, db, attempt, policy, guard, second(payload, reply))
    assert list(db.iterdump()) == before


@pytest.mark.parametrize("mutation", ["cancel", "grant", "owner", "config", "source"])
def test_late_fee_is_saved_but_reply_after_changed_authority_is_not_accepted(
    config, owner, attached, mutation
):
    db, policy, guard, attempt = attached
    call, _ = rounds.dispatch(config, db, attempt, policy, guard, first(db, attempt, policy))
    if mutation == "cancel":
        execution.cancel(db, attempt.client_id, attempt.task_id)
    elif mutation == "grant":
        auth.set_grant(db, owner[0], owner[2]["personal"], query=False, original=False, add=True)
    elif mutation == "owner":
        guard = execution.Responsibility(attempt.client_id, attempt.task_id)
    elif mutation == "config":
        policy = replace(policy, capability_revision="changed")
    else:
        (config.project_root / "test vault/temp_vault1/new.md").write_text("pressure 90 psi")
    with pytest.raises((InvalidInput, IntegrityError, auth.Denied)):
        rounds.accept(config, db, call, policy, guard, body(policy))
    fee = db.execute("SELECT * FROM model_calls WHERE id=?", (call.call_id,)).fetchone()
    assert fee["state"] == "settled" and fee["charged_nusd"] == 10 * 150 + 20 * 500
    assert runtime.rows(db, attempt.run_id)[0]["response_json"] is None


@pytest.mark.parametrize(
    "mutation", ["missing_usage", "excess", "malformed_choice", "wrong_tool", "duplicate_args"]
)
def test_bad_model_reply_keeps_known_or_uncertain_fee_and_cannot_complete(
    config, attached, mutation
):
    db, policy, guard, attempt = attached
    call, _ = rounds.dispatch(config, db, attempt, policy, guard, first(db, attempt, policy))
    reply = body(policy)
    if mutation == "missing_usage":
        reply.pop("usage")
    elif mutation == "excess":
        reply["usage"]["prompt_tokens"] = policy.policy().max_input_tokens + 1
    elif mutation == "malformed_choice":
        reply["choices"] = []
    elif mutation == "wrong_tool":
        reply["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = "bash"
    else:
        reply["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = (
            '{"draft":"a","draft":"b"}'
        )
    with pytest.raises(InvalidInput):
        rounds.accept(config, db, call, policy, guard, reply)
    fee = db.execute("SELECT * FROM model_calls WHERE id=?", (call.call_id,)).fetchone()
    assert fee["state"] == ("uncertain" if mutation == "missing_usage" else "settled")
    assert runtime.rows(db, attempt.run_id)[0]["response_json"] is None
    assert not db.execute("SELECT * FROM document_creation_artifacts").fetchall()


def test_registration_is_durable_idempotent_and_is_not_delivery_or_knowledge_save(
    config, attached, tmp_path
):
    db, policy, guard, attempt = attached
    finish_rounds(config, attached)
    raw = output_file(config, attached, tmp_path)
    metadata = artifacts.register(config, db, attempt, policy, guard, tmp_path)
    assert metadata["version"] == 1 and metadata["execution_mode"] == "synthetic"
    assert (
        metadata["delivery_allowed"]
        is metadata["save_allowed"]
        is metadata["knowledge_verified"]
        is False
    )
    before = list(db.iterdump())
    assert artifacts.register(config, db, attempt, policy, guard, tmp_path) == metadata
    assert list(db.iterdump()) == before
    assert artifacts.get(config, db, attempt.client_id, attempt.task_id) == (raw, metadata)
    assert execution.status(db, attempt.client_id, attempt.task_id)["state"] == "succeeded"
    stored = config.state_root / "document-drafts" / attempt.run_id / "draft.md"
    assert stored.stat().st_mode & 0o777 == 0o600
    (tmp_path / "draft.md").write_bytes(b"worker tamper")
    assert artifacts.get(config, db, attempt.client_id, attempt.task_id)[0] == raw
    stored.write_bytes(b"persistent tamper")
    with pytest.raises((IntegrityError, InvalidInput)):
        artifacts.get(config, db, attempt.client_id, attempt.task_id)


def test_output_cannot_differ_from_the_billed_tool_arguments(config, attached, tmp_path):
    db, policy, guard, attempt = attached
    finish_rounds(config, attached)
    output_file(config, attached, tmp_path)
    plan = tasks.plan(config, db, attempt.client_id, attempt.task_id)
    bound = binding.get(db, attempt.task_id)
    write_output(
        tmp_path,
        plan,
        output.context(plan, attempt.run_id, policy.sha256, bound["request_sha256"]),
        (output.DRAFT_MARKER + "different").encode(),
    )
    before = list(db.iterdump())
    with pytest.raises(InvalidInput, match="differs_from_response"):
        artifacts.register(config, db, attempt, policy, guard, tmp_path)
    assert list(db.iterdump()) == before


def test_registration_requires_all_rounds_and_known_responses(config, attached, tmp_path):
    db, policy, guard, attempt = attached
    call, _ = rounds.dispatch(config, db, attempt, policy, guard, first(db, attempt, policy))
    rounds.accept(config, db, call, policy, guard, body(policy))
    output_file(config, attached, tmp_path)
    with pytest.raises(InvalidInput, match="rounds_incomplete"):
        artifacts.register(config, db, attempt, policy, guard, tmp_path)


def test_default_off_synthetic_runner_has_no_process_reservation_or_provider(
    config, owner, prepared_task, creation_policy
):
    with (
        patch("subprocess.Popen", side_effect=AssertionError("process")),
        patch("fiber_bundle.model_gateway.complete", side_effect=AssertionError("provider")),
    ):
        with pytest.raises(InvalidInput, match="not_enabled"):
            runner.run_synthetic(
                config,
                owner[0],
                prepared_task["task_id"],
                parse(creation_policy),
                "draft",
                expected_revision=1,
                expected_plan_sha256=prepared_task["plan_sha256"],
                expected_configuration_sha256=parse(creation_policy).sha256,
            )
    db = database.connect(config.state_root)
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    db.close()


@pytest.mark.parametrize(
    "prefix", ["INSERT INTO model_calls", "INSERT INTO document_creation_calls", "COMMIT"]
)
def test_second_dispatch_fault_rolls_back_request_and_fee_together(config, attached, prefix):
    db, policy, guard, attempt = attached
    call, payload = rounds.dispatch(config, db, attempt, policy, guard, first(db, attempt, policy))
    reply = rounds.accept(config, db, call, policy, guard, body(policy))

    class Fault(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith(prefix) and prefix == "COMMIT":
                raise sqlite3.OperationalError("synthetic round fault")
            result = super().execute(sql, parameters)
            if sql.startswith(prefix):
                raise sqlite3.OperationalError("synthetic round fault")
            return result

    fault = sqlite3.connect(
        config.state_root / "metadata.sqlite3", isolation_level=None, factory=Fault
    )
    fault.row_factory = sqlite3.Row
    fault.execute("PRAGMA foreign_keys=ON")
    before = list(db.iterdump())
    try:
        with pytest.raises(sqlite3.OperationalError):
            rounds.dispatch(config, fault, attempt, policy, guard, second(payload, reply))
    finally:
        fault.close()
    assert list(db.iterdump()) == before
    next_call, _ = rounds.dispatch(config, db, attempt, policy, guard, second(payload, reply))
    assert next_call.call_id != attempt.call_id


@pytest.mark.parametrize(
    "prefix", ["INSERT INTO document_creation_artifacts", "UPDATE execution_runs", "COMMIT"]
)
def test_registration_fault_keeps_durable_orphan_and_retry_never_regenerates(
    config, attached, tmp_path, prefix
):
    db, policy, guard, attempt = attached
    finish_rounds(config, attached)
    raw = output_file(config, attached, tmp_path)

    class Fault(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith(prefix) and prefix == "COMMIT":
                raise sqlite3.OperationalError("synthetic artifact commit fault")
            result = super().execute(sql, parameters)
            if sql.startswith(prefix):
                raise sqlite3.OperationalError("synthetic artifact write fault")
            return result

    fault = sqlite3.connect(
        config.state_root / "metadata.sqlite3", isolation_level=None, factory=Fault
    )
    fault.row_factory = sqlite3.Row
    fault.execute("PRAGMA foreign_keys=ON")
    before = list(db.iterdump())
    try:
        with pytest.raises(sqlite3.OperationalError):
            artifacts.register(config, fault, attempt, policy, guard, tmp_path)
    finally:
        fault.close()
    assert list(db.iterdump()) == before
    stored = config.state_root / "document-drafts" / attempt.run_id / "draft.md"
    assert stored.read_bytes() == raw
    before_mtime = stored.stat().st_mtime_ns
    with patch("fiber_bundle.model_gateway.complete", side_effect=AssertionError("regeneration")):
        artifacts.register(config, db, attempt, policy, guard, tmp_path)
    assert stored.stat().st_mtime_ns == before_mtime
    assert len(db.execute("SELECT * FROM model_calls").fetchall()) == 2


@pytest.mark.parametrize(
    "mutation", ["request_hash", "response_hash", "profile", "fee", "artifact"]
)
def test_persistent_runtime_tampering_is_rejected(config, attached, tmp_path, mutation):
    db, policy, guard, attempt = attached
    finish_rounds(config, attached)
    output_file(config, attached, tmp_path)
    artifacts.register(config, db, attempt, policy, guard, tmp_path)
    before = execution.status(db, attempt.client_id, attempt.task_id)
    db.execute("BEGIN IMMEDIATE")
    try:
        if mutation == "request_hash":
            db.execute(
                "UPDATE document_creation_calls SET request_sha256=? WHERE ordinal=1", ("0" * 64,)
            )
        elif mutation == "response_hash":
            db.execute(
                "UPDATE document_creation_calls SET response_sha256=? WHERE ordinal=2", ("0" * 64,)
            )
        elif mutation == "profile":
            db.execute("UPDATE document_creation_sessions SET profile_sha256=?", ("0" * 64,))
        elif mutation == "fee":
            db.execute("UPDATE model_calls SET charged_nusd=1")
        else:
            db.execute("UPDATE document_creation_artifacts SET relative_path='../escape'")
        with pytest.raises(IntegrityError):
            execution.status(db, attempt.client_id, attempt.task_id)
    finally:
        db.execute("ROLLBACK")
    assert execution.status(db, attempt.client_id, attempt.task_id) == before


def test_next_round_atomic_daily_competition_across_distinct_task_owners(
    config, owner, input_task, creation_policy
):
    creation_policy["model_config"].update(
        image=profile.IMAGE,
        max_calls=2,
        daily_calls=3,
        max_input_tokens=131072,
        max_request_bytes=131072,
        task_limit_nusd=100000000,
        daily_limit_nusd=200000000,
    )
    creation_policy["capability_revision"] = profile.REVISION
    policy = parse(creation_policy)
    with database.locked(config):
        db = database.connect(config.state_root)
        pending = [
            tasks.submit(config, db, owner[0], name, input_task[0], "Prepare draft", "md")
            for name in ("one", "two")
        ]
        db.close()
    barrier = threading.Barrier(2)

    def reserve(task):
        with execution.responsibility(config, owner[0], task["task_id"]) as guard:
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    attempt = claim(config, db, owner[0], task, policy, guard)
                    rounds.attach(config, db, attempt, policy, guard)
                    call, payload = rounds.dispatch(
                        config, db, attempt, policy, guard, first(db, attempt, policy)
                    )
                    reply = rounds.accept(config, db, call, policy, guard, body(policy))
                finally:
                    db.close()
            barrier.wait(timeout=10)
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    try:
                        rounds.dispatch(config, db, attempt, policy, guard, second(payload, reply))
                    except execution_ledger.BudgetExceeded:
                        return False
                    return True
                finally:
                    db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(reserve, pending)) == 1
    db = database.connect(config.state_root)
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 3
    assert db.execute("SELECT count(*) FROM document_creation_calls").fetchone()[0] == 3
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not db.execute("PRAGMA foreign_key_check").fetchall()
    db.close()


@pytest.mark.parametrize("kind", ["existing", "symlink", "fsync"])
def test_draft_filesystem_failure_preserves_bytes_without_success(config, attached, tmp_path, kind):
    db, policy, guard, attempt = attached
    finish_rounds(config, attached)
    expected = output_file(config, attached, tmp_path)
    stored = config.state_root / "document-drafts" / attempt.run_id / "draft.md"
    stored.parent.mkdir(parents=True, mode=0o700)
    original = b"preserve existing bytes"
    if kind == "existing":
        stored.write_bytes(original)
    elif kind == "symlink":
        victim = tmp_path / "victim"
        victim.write_bytes(original)
        stored.symlink_to(victim)
    before = list(db.iterdump())
    if kind == "fsync":
        with patch(
            "fiber_bundle.document_creation_artifacts.os.fsync", side_effect=OSError("fault")
        ):
            with pytest.raises(OSError):
                artifacts.register(config, db, attempt, policy, guard, tmp_path)
        assert stored.read_bytes() == expected
        assert artifacts.register(config, db, attempt, policy, guard, tmp_path)["version"] == 1
    else:
        with pytest.raises((InvalidInput, IntegrityError)):
            artifacts.register(config, db, attempt, policy, guard, tmp_path)
        assert stored.read_bytes() == original
        assert list(db.iterdump()) == before
        assert execution.status(db, attempt.client_id, attempt.task_id)["state"] == "running"


def test_recovery_cannot_stop_an_active_owner(config, attached):
    db, _, _, attempt = attached
    before = list(db.iterdump())
    with patch("fiber_bundle.document_creation_runner._stop", side_effect=AssertionError("stop")):
        with pytest.raises(InvalidInput, match="already owned"):
            runner.recover_synthetic(config, attempt.client_id, attempt.task_id)
    assert list(db.iterdump()) == before


@pytest.mark.parametrize("mutation", ["image", "revision", "formats", "sources"])
def test_unqualified_runtime_denied_before_any_reservation_or_process(
    config, owner, prepared_task, creation_policy, mutation
):
    creation_policy["model_config"]["image"] = profile.IMAGE
    creation_policy["capability_revision"] = profile.REVISION
    if mutation == "image":
        creation_policy["model_config"]["image"] = "sha256:" + "0" * 64
    elif mutation == "revision":
        creation_policy["capability_revision"] = "other-revision"
    elif mutation == "formats":
        creation_policy["formats"] = ["txt"]
    policy = parse(creation_policy)
    with (
        patch("subprocess.Popen", side_effect=AssertionError("process")),
        patch(
            "fiber_bundle.document_creation_profile.digest",
            return_value="0" * 64 if mutation == "sources" else None,
        ) as digest_check,
    ):
        if mutation != "sources":
            digest_check.side_effect = digest
        with pytest.raises(InvalidInput, match="unqualified|sources_changed"):
            runner.run_synthetic(
                config,
                owner[0],
                prepared_task["task_id"],
                policy,
                "draft",
                expected_revision=prepared_task["revision"],
                expected_plan_sha256=prepared_task["plan_sha256"],
                expected_configuration_sha256=policy.sha256,
                allow_synthetic=True,
            )
    db = database.connect(config.state_root)
    assert not db.execute("SELECT * FROM model_calls").fetchall()
    assert not db.execute("SELECT * FROM document_creation_runs").fetchall()
    db.close()
