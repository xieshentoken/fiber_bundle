"""Cloud Agent reservations share the existing USD ceiling; failures retain their slot."""

import json
import sqlite3
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..config import InvalidInput
from ..usage_credentials import from_model


def totals(db: sqlite3.Connection, day: str) -> tuple[int, int]:
    count = cost = 0
    for row in db.execute(
        "SELECT payload_json,result_json FROM agent_connection_operations "
        "WHERE kind='test_query' AND json_extract(payload_json,"
        "'$.cloud_reservation.billing_day')=?",
        (day,),
    ):
        reservation = json.loads(row[0])["cloud_reservation"]
        result = json.loads(row[1]) if row[1] else {}
        charged = result.get("cloud_cost_nusd", reservation["reserved_nusd"])
        if type(charged) is not int or not 0 <= charged <= reservation["reserved_nusd"]:
            raise InvalidInput("agent_cloud_ledger_invalid")
        count += 1
        cost += charged
    return count, cost


def admit(db: sqlite3.Connection, model: dict[str, Any]) -> dict[str, Any]:
    """Called in the same storage transaction that persists the operation intent."""
    day = datetime.now(ZoneInfo(model["timezone"])).date().isoformat()
    amount = (
        model["max_input_tokens"] * model["input_nusd"]
        + model["max_output_tokens"] * model["output_nusd"]
    )
    count, cost = totals(db, day)
    existing = db.execute(
        "SELECT count(*),coalesce(sum(coalesce(charged_nusd,reserved_nusd)),0) "
        "FROM model_calls WHERE billing_day=?",
        (day,),
    ).fetchone()
    from .. import model_budgets

    shared = model_budgets.key(db, model["secret_file"]) is not None
    if (
        (not shared and count + existing[0] >= model["daily_calls"])
        or (not shared and cost + existing[1] + amount > model["daily_limit_nusd"])
        or amount > model["task_limit_nusd"]
    ):
        raise InvalidInput("agent_cloud_budget_exhausted")
    return {
        **from_model(model, db),
        "budget_reservation_id": model_budgets.reserve(
            db, model["secret_file"], model["max_input_tokens"] + model["max_output_tokens"], amount
        ),
        "billing_day": day,
        "reserved_nusd": amount,
        "input_nusd": model["input_nusd"],
        "output_nusd": model["output_nusd"],
        "configuration_sha256": model["configuration_sha256"],
        "provider": model.get("provider", "opencodego"),
        "price_revision": model.get("price_revision"),
        "model": model["model"],
    }


def charged(reservation: dict[str, Any], usage: dict[str, Any] | None) -> int:
    if usage is None:
        return int(reservation["reserved_nusd"])
    amount = (
        usage["prompt_tokens"] * reservation["input_nusd"]
        + usage["completion_tokens"] * reservation["output_nusd"]
    )
    if not 0 <= amount <= reservation["reserved_nusd"]:
        raise InvalidInput("agent_cloud_usage_invalid")
    return int(amount)
