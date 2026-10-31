"""A video-wide token budget over the existing immutable API ledgers.

Retries keep the job identity and previous run IDs. Reservations are checked
inside the API ledger transaction; missing usage retains the reserved tokens.
No second cost ledger or schema migration is needed.
"""

import json
import sqlite3
from typing import Any

from .config import InvalidInput
from .inputs import canonical
from .model_budgets import Exhausted

DEFAULT_LIMIT = 500_000
MAX_LIMIT = 1_000_000_000


def validate_limit(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= MAX_LIMIT:
        raise InvalidInput("video_skill_token_budget_invalid")
    return value


def _runs(frozen: dict[str, Any], names: tuple[str, ...]) -> list[str]:
    ids: set[str] = set()
    for name in names:
        ids.update(frozen.get(name, {}).values())
        ids.update(row["run_id"] for row in frozen.get("previous_" + name, []))
    return sorted(ids)


def descriptor(frozen: dict[str, Any]) -> dict[str, Any]:
    saved = frozen.get("token_budget", {})
    limit = saved.get(
        "limit", frozen.get("skill", {}).get("settings", {}).get("max_video_tokens", DEFAULT_LIMIT)
    )
    return {
        "job_id": frozen["job_id"],
        "limit": validate_limit(limit),
        "legacy_runs": saved.get("legacy_runs", _runs(frozen, ("vision_runs", "note_runs"))),
        "legacy_asr_runs": saved.get("legacy_asr_runs", _runs(frozen, ("asr_runs",))),
    }


def configure(frozen: dict[str, Any], limit: int | None = None) -> None:
    value = descriptor(frozen)
    if limit is not None:
        value["limit"] = validate_limit(limit)
        if frozen.get("call_allowance"):
            frozen.setdefault("previous_call_allowances", []).append(frozen.pop("call_allowance"))
    frozen["token_budget"] = value


def _rows(db: sqlite3.Connection, budget: dict[str, Any], *, asr: bool) -> list[sqlite3.Row]:
    if asr:
        select = (
            "SELECT c.id,c.input_tokens,c.output_tokens,r.unit,r.amount,r.charged,"
            "json_extract(c.policy_json,'$.video_token_budget.reserved_tokens') reserved "
            "FROM model_configuration_calls c "
            "LEFT JOIN model_budget_reservations r ON r.id=c.reservation_id WHERE "
        )
        match = "json_extract(c.policy_json,'$.job_id')=?"
        legacy = budget["legacy_asr_runs"]
        column = "c.id"
    else:
        select = (
            "SELECT c.id,c.input_tokens,c.output_tokens,r.unit,r.amount,r.charged,"
            "json_extract(p.input_json,'$.video_token_budget.reserved_tokens') reserved "
            "FROM model_calls c JOIN pi_processing_runs p ON p.run_id=c.run_id "
            "LEFT JOIN model_call_budget_bindings b ON b.call_id=c.id "
            "LEFT JOIN model_budget_reservations r ON r.id=b.reservation_id WHERE "
        )
        match = "json_extract(p.input_json,'$.video_token_budget.job_id')=?"
        legacy = budget["legacy_runs"]
        column = "c.run_id"
    found = {r["id"]: r for r in db.execute(select + match, (budget["job_id"],))}
    for start in range(0, len(legacy), 500):
        part = legacy[start : start + 500]
        for row in db.execute(select + column + " IN (" + ",".join("?" for _ in part) + ")", part):
            found[row["id"]] = row
    return list(found.values())


def status(db: sqlite3.Connection, budget: dict[str, Any]) -> dict[str, Any]:
    limit = validate_limit(budget["limit"])
    actual = reserved = unknown = 0
    incoming = outgoing = 0
    for row in [*_rows(db, budget, asr=False), *_rows(db, budget, asr=True)]:
        if type(row["input_tokens"]) is int and type(row["output_tokens"]) is int:
            incoming += row["input_tokens"]
            outgoing += row["output_tokens"]
            actual += row["input_tokens"] + row["output_tokens"]
        elif row["unit"] == "tokens" and type(row["charged"]) is int:
            actual += row["charged"]
        else:
            unknown += 1
            # Old records with money-only reservations cannot establish token
            # usage. Keep them blocked for reconciliation instead of counting 0.
            held = row["reserved"]
            if type(held) is not int:
                held = row["amount"] if row["unit"] == "tokens" else MAX_LIMIT
            reserved += held
    return {
        "limit": limit,
        "input_tokens": incoming,
        "output_tokens": outgoing,
        "actual_tokens": actual,
        "reserved_tokens": reserved,
        "used_tokens": actual + reserved,
        "remaining_tokens": max(0, limit - actual - reserved),
        "unconfirmed_calls": unknown,
    }


def require_remaining(db: sqlite3.Connection, budget: dict[str, Any], tokens: int) -> None:
    if type(tokens) is not int or tokens <= 0:
        raise InvalidInput("video_skill_token_budget_invalid")
    if status(db, budget)["remaining_tokens"] < tokens:
        raise Exhausted("video_skill_token_budget_exhausted")


def binding(frozen: dict[str, Any], tokens: int) -> dict[str, Any]:
    return {**descriptor(frozen), "reserved_tokens": tokens}


def bind(db: sqlite3.Connection, run_id: str, frozen: dict[str, Any], tokens: int) -> None:
    row = db.execute(
        "SELECT input_json FROM pi_processing_runs WHERE run_id=?", (run_id,)
    ).fetchone()
    if row is None or db.execute("SELECT 1 FROM model_calls WHERE run_id=?", (run_id,)).fetchone():
        raise InvalidInput("video_skill_token_budget_invalid")
    value = json.loads(row[0])
    value["video_token_budget"] = binding(frozen, tokens)
    db.execute(
        "UPDATE pi_processing_runs SET input_json=? WHERE run_id=?", (canonical(value), run_id)
    )


def check_reservation(db: sqlite3.Connection, run_id: str, tokens: int) -> None:
    row = db.execute(
        "SELECT json_extract(input_json,'$.video_token_budget') "
        "FROM pi_processing_runs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if row and row[0] is not None:
        budget = json.loads(row[0])
        if budget["reserved_tokens"] != tokens:
            raise InvalidInput("video_skill_token_budget_invalid")
        require_remaining(db, budget, tokens)
