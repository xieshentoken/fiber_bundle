"""Read-only checks of an owned current head and shared daily fees; no run or reservation.

Callers hold database.locked(config). A preview is neither a durable budget claim
nor an executor capability certification; all runtime gates remain closed.
"""

import re
import sqlite3
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from . import document_creation_tasks as tasks
from .config import Config, IntegrityError, InvalidInput
from .document_creation_config import CreationExecutionConfig
from .execution_ledger import BudgetExceeded
from .inputs import canonical
from .objects import digest


def preview(
    config: Config,
    main: sqlite3.Connection,
    client_id: str,
    task_id: str,
    policy: CreationExecutionConfig,
    *,
    expected_revision: int,
    expected_plan_sha256: str,
    expected_configuration_sha256: str,
) -> dict[str, Any]:
    """Bind declarations to the actual head; never use caller-supplied plans or spend totals."""
    plan = tasks.plan(config, main, client_id, task_id)
    if (
        type(expected_revision) is not int
        or not isinstance(expected_plan_sha256, str)
        or re.fullmatch(r"[a-f0-9]{64}", expected_plan_sha256) is None
        or (plan.revision, plan.sha256) != (expected_revision, expected_plan_sha256)
    ):
        raise InvalidInput("document_plan_conflict")
    model = policy.policy()
    if expected_configuration_sha256 != policy.sha256:
        raise InvalidInput("document_configuration_changed")
    if plan.requested_format not in policy.formats:
        raise InvalidInput("document_format_not_declared")
    if not plan.value()["input"]["sources"]:
        raise InvalidInput("document_input_insufficient")
    # Same billing-day/global USD pool and unknown-fee rule as execution_ledger.
    day = datetime.now(ZoneInfo(model.timezone)).date().isoformat()
    count, cost, invalid = main.execute(
        "SELECT count(*),coalesce(sum(coalesce(charged_nusd,reserved_nusd)),0),"
        "coalesce(sum(CASE WHEN currency!='USD' OR typeof(reserved_nusd)!='integer' "
        "OR reserved_nusd<=0 OR (charged_nusd IS NOT NULL AND "
        "(typeof(charged_nusd)!='integer' OR charged_nusd<0)) "
        "OR state NOT IN ('reserved','settled','uncertain') "
        "OR (state='settled' AND charged_nusd IS NULL) THEN 1 ELSE 0 END),0) "
        "FROM model_calls WHERE billing_day=?",
        (day,),
    ).fetchone()
    if invalid:
        raise IntegrityError("document_budget_ledger_invalid")
    if count >= model.daily_calls or cost + model.reserve_nusd > model.daily_limit_nusd:
        raise BudgetExceeded("document_daily_budget_exhausted")
    result = {
        "schema": "document-creation-admission-preview/v1",
        "state": "prepared",
        "task_id": task_id,
        "plan_sha256": plan.sha256,
        "input_sha256": digest(plan.input_json.encode()),
        "revision": plan.revision,
        "requested_format": plan.requested_format,
        "configuration_sha256": policy.sha256,
        "capability_declared": True,
        "capability_verified": False,
        "budget": {
            "currency": model.currency,
            "timezone": model.timezone,
            "billing_day": day,
            "next_call_reserve_nusd": model.reserve_nusd,
            "task_limit_nusd": model.task_limit_nusd,
            "task_call_limit": model.max_calls,
            "daily_limit_nusd": model.daily_limit_nusd,
            "daily_call_limit": model.daily_calls,
            "daily_accounted_nusd": cost,
            "daily_accounted_calls": count,
            "scope": "shared_model_calls",
            "task_budget_declaration_checked": True,
            "daily_budget_available_at_check": True,
            "budget_reserved": False,
        },
        "input_coverage": "retrieved_excerpts",
        "unverified_draft": True,
        "runtime_verified": False,
        "request_limits_checked": False,
        "execution_allowed": False,
        "delivery_allowed": False,
        "save_allowed": False,
        "automatic_switch": False,
        "credentials_read": False,
        "model_calls": 0,
    }
    return {**result, "preview_sha256": digest(canonical(result).encode())}
