"""Durable per-resume API allowance, checked in the existing reservation transaction."""

import json
import sqlite3
from typing import Any
from uuid import UUID, uuid4

from .config import InvalidInput
from .inputs import canonical
from .model_budgets import Exhausted


def validate(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"id", "limit"}:
        raise InvalidInput("video_skill_call_allowance_invalid")
    try:
        if str(UUID(value["id"])) != value["id"]:
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as error:
        raise InvalidInput("video_skill_call_allowance_invalid") from error
    if type(value["limit"]) is not int or not 0 <= value["limit"] <= 1000:
        raise InvalidInput("video_skill_call_allowance_invalid")
    return dict(value)


def new(limit: int) -> dict[str, Any]:
    return validate({"id": str(uuid4()), "limit": limit})


def status(db: sqlite3.Connection, allowance: dict[str, Any]) -> dict[str, Any]:
    value = validate(allowance)
    used = db.execute(
        "SELECT count(*) FROM model_calls c JOIN pi_processing_runs p ON p.run_id=c.run_id "
        "WHERE json_extract(p.input_json,'$.video_call_allowance.id')=?",
        (value["id"],),
    ).fetchone()[0]
    used += db.execute(
        "SELECT count(*) FROM model_configuration_calls "
        "WHERE json_extract(policy_json,'$.video_call_allowance.id')=?",
        (value["id"],),
    ).fetchone()[0]
    return {**value, "used": used, "remaining": max(0, value["limit"] - used)}


def require_remaining(db: sqlite3.Connection, allowance: dict[str, Any] | None) -> None:
    if allowance is not None and status(db, allowance)["remaining"] == 0:
        raise Exhausted("video_skill_call_limit_reached")


def bind(db: sqlite3.Connection, run_id: str, frozen: dict[str, Any]) -> None:
    allowance = frozen.get("call_allowance")
    if allowance is None:
        return
    row = db.execute(
        "SELECT input_json FROM pi_processing_runs WHERE run_id=?", (run_id,)
    ).fetchone()
    if row is None or db.execute("SELECT 1 FROM model_calls WHERE run_id=?", (run_id,)).fetchone():
        raise InvalidInput("video_skill_call_allowance_invalid")
    value = json.loads(row[0])
    value["video_call_allowance"] = validate(allowance)
    db.execute(
        "UPDATE pi_processing_runs SET input_json=? WHERE run_id=?", (canonical(value), run_id)
    )


def check_reservation(db: sqlite3.Connection, run_id: str) -> None:
    row = db.execute(
        "SELECT json_extract(input_json,'$.video_call_allowance') FROM pi_processing_runs "
        "WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if row and row[0] is not None:
        require_remaining(db, json.loads(row[0]))
