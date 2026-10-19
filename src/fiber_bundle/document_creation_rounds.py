"""At-most-once actual Pi requests sharing the atomic model-call budget ledger."""

import json
import sqlite3
import time
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from . import database, execution_ledger
from . import document_creation_binding as binding
from . import document_creation_continuation as continuation
from . import document_creation_dialogue as dialogue
from . import document_creation_execution as execution
from . import document_creation_profile as profile
from . import document_creation_runtime_state as runtime
from .config import Config, InvalidInput
from .document_creation_config import CreationExecutionConfig
from .inputs import canonical
from .objects import digest


def attach(
    config: Config,
    main: sqlite3.Connection,
    attempt: execution.Attempt,
    policy: CreationExecutionConfig,
    owner: execution.Responsibility,
    *,
    mode: str = "synthetic",
) -> None:
    with database.transaction(main):
        plan = execution.verify(config, main, attempt, policy, owner)
        qualified = profile.check(policy, plan.requested_format, mode)
        if (
            main.execute(
                "SELECT 1 FROM document_creation_sessions WHERE run_id=?", (attempt.run_id,)
            ).fetchone()
            is not None
        ):
            raise InvalidInput("document_runtime_not_replayable")
        call = main.execute(
            "SELECT state FROM model_calls WHERE id=?", (attempt.call_id,)
        ).fetchone()
        if call["state"] != "reserved":
            raise InvalidInput("document_runtime_already_accounted")
        raw = canonical(qualified)
        main.execute(
            "INSERT INTO document_creation_sessions VALUES (?,?,?,?)",
            (attempt.run_id, raw, digest(raw.encode()), mode),
        )
        bound = binding.get(main, attempt.task_id)
        assert bound is not None
        binding.checked(main, bound)


def dispatch(
    config: Config,
    main: sqlite3.Connection,
    attempt: execution.Attempt,
    policy: CreationExecutionConfig,
    owner: execution.Responsibility,
    actual: object,
) -> tuple[execution.Attempt, dict[str, Any]]:
    """Commit the complete actual request as dispatched before any host reply/provider effect."""
    with database.transaction(main):
        plan = execution.verify(config, main, attempt, policy, owner)
        profile.check(policy, plan.requested_format)
        session = main.execute(
            "SELECT 1 FROM document_creation_sessions WHERE run_id=?", (attempt.run_id,)
        ).fetchone()
        if session is None:
            raise InvalidInput("document_runtime_not_attached")
        bound = binding.get(main, attempt.task_id)
        assert bound is not None
        records = runtime.rows(main, attempt.run_id)
        prior = None
        if records:
            if len(records) >= 2:
                raise InvalidInput("document_round_limit_reached")
            if records[0]["response_json"] is None:
                raise InvalidInput("document_round_not_replayable")
            prior = (
                json.loads(records[0]["request_json"]),
                json.loads(records[0]["response_json"]),
            )
        payload = dialogue.request(policy.policy(), actual, bound["request_json"], prior)
        model = policy.policy()
        if not records:
            call_id = attempt.call_id
            call = main.execute("SELECT state FROM model_calls WHERE id=?", (call_id,)).fetchone()
            if call["state"] != "reserved":
                raise InvalidInput("document_round_already_accounted")
        else:
            day = datetime.now(ZoneInfo(model.timezone)).date().isoformat()
            continuation.check_budget(main, attempt.task_id, model)
            execution_ledger.check_budget(main, attempt.run_id, model, billing_day=day)
            call_id = str(uuid4())
            main.execute(
                "INSERT INTO model_calls VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,'reserved',?)",
                (
                    call_id,
                    attempt.run_id,
                    model.model,
                    model.price_revision,
                    day,
                    "USD",
                    model.reserve_nusd,
                    time.time(),
                ),
            )
            from . import model_budgets
            from .usage_credentials import record as record_credential

            record_credential(main, call_id, model.secret_file)
            reservation = model_budgets.reserve(
                main,
                model.secret_file,
                model.max_input_tokens + model.max_output_tokens,
                model.reserve_nusd,
            )
            if reservation is not None:
                main.execute(
                    "INSERT INTO model_call_budget_bindings VALUES(?,?)", (call_id, reservation)
                )
        raw = canonical(payload)
        main.execute(
            "INSERT INTO document_creation_calls VALUES (?,?,?,?,?,NULL,NULL)",
            (call_id, attempt.run_id, len(records) + 1, raw, digest(raw.encode())),
        )
        binding.checked(main, bound)
        return replace(attempt, call_id=call_id), payload


def accept(
    config: Config,
    main: sqlite3.Connection,
    attempt: execution.Attempt,
    policy: CreationExecutionConfig,
    owner: execution.Responsibility,
    body: object,
) -> dict[str, Any]:
    """Save known fees first, even after cancellation. Persist only a still-authorized reply."""
    # Verify that a request was actually committed. Legacy accounting remains separate.
    bound = binding.get(main, attempt.task_id)
    if bound is None or bound["run_id"] != attempt.run_id or bound["owner_id"] != attempt.owner_id:
        raise InvalidInput("document_attempt_binding_invalid")
    binding.checked(main, bound)
    record = main.execute(
        "SELECT * FROM document_creation_calls WHERE call_id=? AND run_id=?",
        (attempt.call_id, attempt.run_id),
    ).fetchone()
    if record is None:
        raise InvalidInput("document_round_not_dispatched")
    if record["response_json"] is not None:
        if canonical(body) != record["response_json"]:
            raise InvalidInput("document_response_conflict")
        execution.verify(config, main, attempt, policy, owner)
        return dialogue.response(
            policy.policy(), json.loads(record["response_json"]), record["ordinal"]
        )
    usage = body.get("usage") if isinstance(body, dict) else None
    report = execution.record_usage(main, attempt, usage)
    call = main.execute("SELECT state FROM model_calls WHERE id=?", (attempt.call_id,)).fetchone()
    if report["state"] != "running" or call["state"] != "settled":
        raise InvalidInput("document_response_not_active_or_accounted")
    with database.transaction(main):
        execution.verify(config, main, attempt, policy, owner)
        parsed = dialogue.response(policy.policy(), body, record["ordinal"])
        raw = canonical(parsed)
        main.execute(
            "UPDATE document_creation_calls SET response_json=?,response_sha256=? "
            "WHERE call_id=? AND response_json IS NULL",
            (raw, digest(raw.encode()), attempt.call_id),
        )
        binding.checked(main, bound)
    return parsed


def fail(main: sqlite3.Connection, attempt: execution.Attempt, code: str) -> None:
    if code not in {
        "document_runtime_failed",
        "document_runtime_timeout",
        "document_budget_exhausted",
        "document_runtime_stopped",
    }:
        raise InvalidInput("document_failure_code_invalid")
    bound = binding.get(main, attempt.task_id)
    if bound is None or any(
        bound[k] != v
        for k, v in {
            "run_id": attempt.run_id,
            "client_id": attempt.client_id,
            "owner_id": attempt.owner_id,
            "configuration_sha256": attempt.configuration_sha256,
        }.items()
    ):
        raise InvalidInput("document_attempt_binding_invalid")
    run, _ = binding.checked(main, bound)
    if run["state"] != "running":
        return
    with database.transaction(main):
        main.execute(
            "UPDATE model_calls SET state='uncertain' WHERE run_id=? AND state='reserved'",
            (attempt.run_id,),
        )
        main.execute(
            "UPDATE execution_runs SET state='failed',error_code=?,updated_at=? "
            "WHERE id=? AND state='running'",
            (code, time.time(), attempt.run_id),
        )
