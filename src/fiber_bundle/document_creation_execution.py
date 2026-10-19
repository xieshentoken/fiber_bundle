"""Offline execution ownership, accounting and candidate file inspection; no provider or process."""

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from . import database, execution_ledger
from . import document_creation_admission as admission
from . import document_creation_binding as binding
from . import document_creation_continuation as continuation
from . import document_creation_request as envelope
from . import document_creation_tasks as tasks
from .config import Config, IntegrityError, InvalidInput
from .document_creation_config import CreationExecutionConfig
from .document_creation_contract import CreationPlan
from .document_creation_output import CheckedOutput
from .document_creation_output import context as output_context
from .document_creation_output import inspect as inspect_file_output
from .executor_container import run_lock
from .inputs import canonical
from .objects import digest


@dataclass
class Responsibility:
    client_id: str
    task_id: str
    owner_id: str = field(default_factory=lambda: str(uuid4()), repr=False)
    active: bool = field(default=True, repr=False)


@dataclass(frozen=True)
class Attempt:
    client_id: str
    task_id: str
    run_id: str
    call_id: str
    configuration_sha256: str
    owner_id: str = field(repr=False)


@contextmanager
def responsibility(config: Config, client_id: str, task_id: str) -> Iterator[Responsibility]:
    """Hold outside the main lock; an unfinished release interrupts without replay or refund."""
    with run_lock(config, task_id):
        owner = Responsibility(client_id, task_id)
        try:
            yield owner
        finally:
            owner.active = False
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    bound = binding.get(db, task_id)
                    if bound is not None and bound["owner_id"] == owner.owner_id:
                        binding.stop(db, bound, interrupt=True)
                finally:
                    db.close()


def _owner(
    owner: Responsibility, client_id: str, task_id: str, owner_id: str | None = None
) -> None:
    if (
        not isinstance(owner, Responsibility)
        or owner.active is not True
        or owner.client_id != client_id
        or owner.task_id != task_id
        or (owner_id is not None and owner.owner_id != owner_id)
    ):
        raise InvalidInput("document_execution_owner_invalid")


def claim(
    config: Config,
    main: sqlite3.Connection,
    client_id: str,
    task_id: str,
    policy: CreationExecutionConfig,
    owner: Responsibility,
    *,
    expected_revision: int,
    expected_plan_sha256: str,
    expected_configuration_sha256: str,
    allow_reservation: bool = False,
) -> Attempt:
    """Caller holds the storage lock. Reservation permission never enables inference."""
    if allow_reservation is not True:
        raise InvalidInput("document_reservation_not_enabled")
    _owner(owner, client_id, task_id)
    with database.transaction(main):
        admission.preview(
            config,
            main,
            client_id,
            task_id,
            policy,
            expected_revision=expected_revision,
            expected_plan_sha256=expected_plan_sha256,
            expected_configuration_sha256=expected_configuration_sha256,
        )
        if binding.get(main, task_id) is not None:
            raise InvalidInput("document_attempt_not_replayable")
        plan = tasks.plan(config, main, client_id, task_id)
        model = policy.policy()
        request_json = canonical(
            envelope.request(model, plan, continuation.previous(config, main, client_id, task_id))
        )
        vault_id = plan.value()["input"]["vault_ids"][0]
        run_id, call_id, now = str(uuid4()), str(uuid4()), time.time()
        # notes is a safe anchor; no generic answer/source binding is installed. Legacy
        # execution_ledger.authorize therefore rejects this run, including missing bindings.
        main.execute(
            "INSERT INTO execution_runs "
            "(id,client_id,vault_id,version_id,config_digest,state,attempts,task_limit_nusd,"
            "currency,created_at,updated_at,binding_kind) "
            "VALUES (?,?,?,NULL,?,'running',1,?,'USD',?,?,'notes')",
            (run_id, client_id, vault_id, model.digest, model.task_limit_nusd, now, now),
        )
        main.execute(
            "INSERT INTO document_creation_runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                task_id,
                client_id,
                plan.revision,
                plan.sha256,
                digest(plan.input_json.encode()),
                policy.sha256,
                canonical(policy.value()),
                request_json,
                digest(request_json.encode()),
                owner.owner_id,
            ),
        )
        day = datetime.now(ZoneInfo(model.timezone)).date().isoformat()
        continuation.check_budget(main, task_id, model)
        execution_ledger.check_budget(main, run_id, model, billing_day=day)
        main.execute(
            "INSERT INTO model_calls VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,'reserved',?)",
            (
                call_id,
                run_id,
                model.model,
                model.price_revision,
                day,
                model.currency,
                model.reserve_nusd,
                now,
            ),
        )
        from .usage_credentials import record as record_credential

        record_credential(main, call_id, model.secret_file)
        from . import model_budgets

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
        bound = binding.get(main, task_id)
        assert bound is not None
        binding.checked(main, bound)
    return Attempt(client_id, task_id, run_id, call_id, policy.sha256, owner.owner_id)


def _attempt(main: sqlite3.Connection, attempt: Attempt) -> tuple[sqlite3.Row, sqlite3.Row]:
    bound = binding.get(main, attempt.task_id)
    if bound is None or any(
        bound[k] != v
        for k, v in {
            "client_id": attempt.client_id,
            "run_id": attempt.run_id,
            "configuration_sha256": attempt.configuration_sha256,
            "owner_id": attempt.owner_id,
        }.items()
    ):
        raise InvalidInput("document_attempt_binding_invalid")
    run, _ = binding.checked(main, bound)
    call = main.execute(
        "SELECT * FROM model_calls WHERE id=? AND run_id=?", (attempt.call_id, attempt.run_id)
    ).fetchone()
    if not isinstance(call, sqlite3.Row):
        raise InvalidInput("document_attempt_binding_invalid")
    return run, bound


def current(
    config: Config,
    main: sqlite3.Connection,
    attempt: Attempt,
    policy: CreationExecutionConfig,
    owner: Responsibility,
) -> dict[str, Any]:
    plan, _ = _current_plan(config, main, attempt, policy, owner)
    call = main.execute("SELECT state FROM model_calls WHERE id=?", (attempt.call_id,)).fetchone()
    if call["state"] != "reserved":
        raise InvalidInput("document_request_already_accounted")
    return envelope.request(
        policy.policy(),
        plan,
        continuation.previous(config, main, attempt.client_id, attempt.task_id),
    )


def _current_plan(
    config: Config,
    main: sqlite3.Connection,
    attempt: Attempt,
    policy: CreationExecutionConfig,
    owner: Responsibility,
) -> tuple[CreationPlan, sqlite3.Row]:
    _owner(owner, attempt.client_id, attempt.task_id, attempt.owner_id)
    run, bound = _attempt(main, attempt)
    if run["state"] != "running":
        raise InvalidInput("document_execution_not_active")
    if policy.sha256 != attempt.configuration_sha256:
        raise InvalidInput("document_configuration_changed")
    plan = tasks.plan(config, main, attempt.client_id, attempt.task_id)
    if run["vault_id"] != plan.value()["input"]["vault_ids"][0]:
        raise IntegrityError("document_execution_binding_changed")
    wire = canonical(
        envelope.request(
            policy.policy(),
            plan,
            continuation.previous(config, main, attempt.client_id, attempt.task_id),
        )
    )
    if (plan.revision, plan.sha256, digest(plan.input_json.encode()), wire) != (
        bound["revision"],
        bound["plan_sha256"],
        bound["input_sha256"],
        bound["request_json"],
    ):
        raise IntegrityError("document_execution_binding_changed")
    return plan, bound


def verify(
    config: Config,
    main: sqlite3.Connection,
    attempt: Attempt,
    policy: CreationExecutionConfig,
    owner: Responsibility,
) -> CreationPlan:
    """Live input authority for the dedicated runtime; this is not permission to call a model."""
    return _current_plan(config, main, attempt, policy, owner)[0]


def inspect_output(
    config: Config,
    main: sqlite3.Connection,
    attempt: Attempt,
    policy: CreationExecutionConfig,
    owner: Responsibility,
    task_directory: Path,
    *,
    upload_limit_bytes: int | None = None,
) -> CheckedOutput:
    """Check a candidate after known usage; do not register completion or enable delivery."""
    plan, bound = _current_plan(config, main, attempt, policy, owner)
    call = main.execute("SELECT state FROM model_calls WHERE id=?", (attempt.call_id,)).fetchone()
    if call["state"] != "settled":
        raise InvalidInput("document_output_usage_unsettled")
    result = inspect_file_output(
        task_directory,
        plan,
        output_context(plan, attempt.run_id, policy.sha256, bound["request_sha256"]),
        upload_limit_bytes=upload_limit_bytes,
    )
    _current_plan(config, main, attempt, policy, owner)
    return result


def status(main: sqlite3.Connection, client_id: str, task_id: str) -> dict[str, Any]:
    return binding.report(main, binding.owned(main, client_id, task_id))


def cancel(main: sqlite3.Connection, client_id: str, task_id: str) -> dict[str, Any]:
    bound = binding.owned(main, client_id, task_id)
    binding.stop(main, bound)
    return binding.report(main, bound)


def recover(config: Config, client_id: str, task_id: str) -> dict[str, Any]:
    """Acquire the task lock; active owners block recovery, interrupted attempts stay spent."""
    with run_lock(config, task_id), database.locked(config):
        main = database.connect(config.state_root)
        try:
            bound = binding.owned(main, client_id, task_id)
            binding.stop(main, bound, interrupt=True)
            return binding.report(main, bound)
        finally:
            main.close()


def record_usage(main: sqlite3.Connection, attempt: Attempt, usage: object) -> dict[str, Any]:
    """Accept known late fees after revocation/cancel; never accept an artifact or revive a run."""
    with database.transaction(main):
        run, bound = _attempt(main, attempt)
        _, policy = binding.checked(main, bound)
        call = main.execute("SELECT * FROM model_calls WHERE id=?", (attempt.call_id,)).fetchone()
        if call["state"] == "settled":
            if not isinstance(usage, dict) or any(
                type(usage.get(k)) is not int or usage[k] != call[column]
                for k, column in (
                    ("prompt_tokens", "input_tokens"),
                    ("completion_tokens", "output_tokens"),
                )
            ):
                raise InvalidInput("document_usage_conflict")
        else:
            try:
                execution_ledger.settle(
                    main, attempt.call_id, policy.policy(), usage, reconcile=True
                )
            except InvalidInput:
                # settle can record known excessive usage before reporting overrun. Preserve it.
                main.execute(
                    "UPDATE model_calls SET state='uncertain' WHERE id=? AND state='reserved'",
                    (attempt.call_id,),
                )
                if run["state"] == "running":
                    main.execute(
                        "UPDATE execution_runs SET state='failed',"
                        "error_code='document_usage_rejected',"
                        "updated_at=? WHERE id=?",
                        (time.time(), attempt.run_id),
                    )
    return binding.report(main, bound)
