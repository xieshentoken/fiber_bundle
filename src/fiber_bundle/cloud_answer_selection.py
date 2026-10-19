"""Explicit host approval for a frozen backup, only before any cloud attempt.

Callers hold the storage lock. Selection never reads credentials or calls a model.
"""

import sqlite3
import time
from typing import Any
from uuid import uuid4

from . import auth, database
from . import cloud_answer_binding as binding
from . import execution_ledger as ledger
from .config import Config, InvalidInput
from .execution_config import ExecutionConfig
from .inputs import text
from .query_routing_config import QueryRoutingConfig


def resolve(
    db: sqlite3.Connection, route: QueryRoutingConfig, run: sqlite3.Row, bound: sqlite3.Row
) -> tuple[ExecutionConfig, int | None]:
    if bound["routing_digest"] != route.digest:
        raise InvalidInput("cloud_configuration_changed")
    row = db.execute(
        "SELECT * FROM cloud_answer_selections WHERE run_id=? AND state='selected'", (run["id"],)
    ).fetchone()
    index = None if row is None else row["backup_index"]
    policy = route.answer_policy(index)
    if run["config_digest"] != policy.digest or (
        row is not None
        and (
            row["client_id"] != run["client_id"]
            or row["routing_digest"] != route.digest
            or row["previous_digest"] != route.answer_policy().digest
            or row["selected_digest"] != policy.digest
            or policy.timezone != route.answer_policy().timezone
            or run["task_limit_nusd"]
            > min(policy.task_limit_nusd, route.answer_policy().task_limit_nusd)
        )
    ):
        raise InvalidInput("cloud_configuration_changed")
    return policy, index


def report(db: sqlite3.Connection, client_id: str, run_id: str) -> list[dict[str, Any]]:
    binding.owned(db, client_id, run_id)
    active = db.execute("SELECT active FROM clients WHERE id=?", (client_id,)).fetchone()
    if active is None or not active[0]:
        raise auth.Denied(401)
    return [
        {
            k: row[k]
            for k in (
                "selection_id",
                "backup_index",
                "previous_digest",
                "selected_digest",
                "state",
                "reason",
                "approval_kind",
                "created_at",
            )
        }
        for row in db.execute(
            "SELECT * FROM cloud_answer_selections WHERE run_id=? AND client_id=? "
            "ORDER BY created_at,id",
            (run_id, client_id),
        )
    ]


def select(
    config: Config,
    db: sqlite3.Connection,
    route: QueryRoutingConfig,
    client_id: str,
    run_id: str,
    backup_index: int,
    selection_id: str,
    *,
    allow_switch: bool = False,
) -> dict[str, Any]:
    if allow_switch is not True:
        raise InvalidInput("cloud_switch_not_enabled")
    route.validate()
    target = route.answer_policy(backup_index)
    text(selection_id, limit=128)
    with database.transaction(db):
        run, bound = binding.owned(db, client_id, run_id)
        answer = binding.envelope(binding.current(config, db, run, bound))
        if bound["routing_digest"] != route.digest:
            raise InvalidInput("cloud_configuration_changed")
        prior = db.execute(
            "SELECT * FROM cloud_answer_selections WHERE run_id=? AND selection_id=?",
            (run_id, selection_id),
        ).fetchone()
        if prior is not None:
            if prior["backup_index"] != backup_index or prior["routing_digest"] != route.digest:
                raise InvalidInput("cloud_selection_conflict")
            return {
                "run_id": run_id,
                "state": prior["state"],
                "reason": prior["reason"],
                "automatic_switch": False,
                "selections": report(db, client_id, run_id),
            }
        reason = "cloud_backup_selected"
        state = "selected"
        if (
            run["state"] != "queued"
            or run["attempts"] != 0
            or db.execute("SELECT 1 FROM model_calls WHERE run_id=?", (run_id,)).fetchone()
        ):
            reason = "cloud_switch_already_attempted"
        elif db.execute(
            "SELECT 1 FROM cloud_answer_selections WHERE run_id=? AND state='selected'", (run_id,)
        ).fetchone():
            reason = "cloud_backup_already_selected"
        elif run["config_digest"] != route.answer_policy().digest:
            reason = "cloud_configuration_changed"
        elif target.timezone != route.answer_policy().timezone:
            reason = "cloud_backup_timezone_mismatch"
        else:
            try:
                binding.prepare(route, answer, backup_index=backup_index)
                # A backup may cost less, but cannot expand the original task/day ceilings.
                ledger.check_budget(
                    db, run_id, route.answer_policy(), reserve_nusd=target.reserve_nusd
                )
                ledger.check_budget(db, run_id, target)
            except ledger.BudgetExceeded:
                reason = "cloud_backup_budget_exceeded"
            except InvalidInput:
                reason = "cloud_backup_request_rejected"
        if reason != "cloud_backup_selected":
            state = "refused"
        else:
            db.execute(
                "UPDATE execution_runs SET config_digest=?,task_limit_nusd=?,updated_at=? "
                "WHERE id=?",
                (
                    target.digest,
                    min(run["task_limit_nusd"], target.task_limit_nusd),
                    time.time(),
                    run_id,
                ),
            )
        db.execute(
            "INSERT INTO cloud_answer_selections VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(uuid4()),
                run_id,
                client_id,
                selection_id,
                route.digest,
                backup_index,
                run["config_digest"],
                target.digest,
                state,
                reason,
                "trusted_host_explicit",
                time.time(),
            ),
        )
        return {
            "run_id": run_id,
            "state": state,
            "reason": reason,
            "automatic_switch": False,
            "selections": report(db, client_id, run_id),
        }
