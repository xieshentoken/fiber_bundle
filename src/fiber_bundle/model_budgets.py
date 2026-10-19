"""One host-owned budget pool per credential; callers own the enclosing transaction."""

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator

from .config import InvalidInput
from .inputs import canonical
from .payloads import Payload


class Exhausted(InvalidInput):
    pass


class Policy(Payload):
    label: str = Field(min_length=1, max_length=100)
    unit: Literal["tokens", "USD_nano"] = "tokens"
    limit: int = Field(ge=0, le=10**12)
    period: Literal["day", "month"] = "day"
    timezone: str = "Asia/Taipei"

    @field_validator("timezone")
    @classmethod
    def zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("budget_timezone_invalid") from None
        return value


class Save(Payload):
    secret_file: str | None = Field(default=None, max_length=2000)
    credential_id: str | None = Field(default=None, pattern=r"^key_[a-f0-9]{64}$")
    expected_revision: int = Field(ge=0)
    settings: Policy


def key(db: sqlite3.Connection, secret_file: str) -> str | None:
    row = db.execute(
        "SELECT credential_id FROM model_credential_aliases WHERE secret_file=?", (secret_file,)
    ).fetchone()
    return row[0] if row else None


def get(db: sqlite3.Connection, identity: str) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT * FROM model_budget_policies WHERE credential_id=?", (identity,)
    ).fetchone()
    return {**dict(row), "settings": json.loads(row["value"])} if row else None


def period(policy: Policy) -> str:
    now = datetime.now(ZoneInfo(policy.timezone))
    return now.strftime("%Y-%m" if policy.period == "month" else "%Y-%m-%d")


def totals(db: sqlite3.Connection, identity: str, cycle: str) -> int:
    return int(
        db.execute(
            "SELECT coalesce(sum(coalesce(charged,amount)),0) FROM model_budget_reservations "
            "WHERE credential_id=? AND period=?",
            (identity, cycle),
        ).fetchone()[0]
    )


def inspect(db: sqlite3.Connection) -> list[dict[str, Any]]:
    result = []
    for row in db.execute("SELECT credential_id FROM model_budget_policies ORDER BY updated_at"):
        saved = get(db, row[0])
        assert saved is not None
        policy = Policy.model_validate(saved["settings"])
        cycle = period(policy)
        used = totals(db, row[0], cycle)
        result.append(
            {
                "credential_id": row[0],
                "revision": saved["revision"],
                "settings": policy.model_dump(),
                "period": cycle,
                "used": used,
                "remaining": max(0, policy.limit - used),
                "provider_balance": None,
            }
        )
    return result


def save(db: sqlite3.Connection, data: Save) -> dict[str, Any]:
    from .usage_credentials import reference

    if data.secret_file is None:
        raise InvalidInput("budget_credential_path_required")
    path = Path(data.secret_file)
    if not path.is_absolute() or path.is_symlink():
        raise InvalidInput("budget_credential_path_invalid")
    mapped = key(db, data.secret_file)
    identity = data.credential_id or mapped or reference(data.secret_file)["id"]
    if mapped is not None and mapped != identity:
        raise InvalidInput("budget_credential_already_bound")
    current = get(db, identity)
    if (current["revision"] if current else 0) != data.expected_revision:
        raise InvalidInput("budget_revision_changed")
    if current:
        previous = Policy.model_validate(current["settings"])
        if (previous.unit, previous.period, previous.timezone) != (
            data.settings.unit,
            data.settings.period,
            data.settings.timezone,
        ):
            # Changing accounting units/calendar cannot reset active reservations.
            raise InvalidInput("budget_accounting_basis_immutable")
    db.execute(
        "INSERT INTO model_budget_policies VALUES(?,?,?,?) "
        "ON CONFLICT(credential_id) DO UPDATE SET revision=excluded.revision,"
        "value=excluded.value,updated_at=excluded.updated_at",
        (identity, data.expected_revision + 1, canonical(data.settings.model_dump()), time.time()),
    )
    db.execute(
        "INSERT OR IGNORE INTO model_credential_aliases VALUES(?,?,?)",
        (data.secret_file, identity, reference(data.secret_file)["id"]),
    )
    # Adopt previously recorded calls under this exact file reference; no secret read.
    old_id = reference(data.secret_file)["id"]
    db.execute(
        "UPDATE model_call_credentials SET credential_id=?,label=? WHERE credential_id=?",
        (identity, data.settings.label, old_id),
    )
    adopt_history(db, identity, old_id, data.settings)
    return next(row for row in inspect(db) if row["credential_id"] == identity)


def adopt_history(db: sqlite3.Connection, identity: str, old_id: str, policy: Policy) -> None:
    """Configuring a cap never makes earlier consumption disappear."""
    for row in db.execute(
        "SELECT c.* FROM model_calls c JOIN model_call_credentials k ON k.call_id=c.id "
        "LEFT JOIN model_call_budget_bindings b ON b.call_id=c.id "
        "WHERE k.credential_id=? AND b.call_id IS NULL",
        (identity,),
    ).fetchall():
        cycle = datetime.fromtimestamp(row["created_at"], ZoneInfo(policy.timezone)).strftime(
            "%Y-%m" if policy.period == "month" else "%Y-%m-%d"
        )
        known = row["input_tokens"] is not None and row["output_tokens"] is not None
        amount = (
            (row["input_tokens"] + row["output_tokens"] if known else policy.limit)
            if policy.unit == "tokens"
            else (row["charged_nusd"] if row["charged_nusd"] is not None else row["reserved_nusd"])
        )
        rid = "historical:" + row["id"]
        state = "settled" if row["state"] == "settled" and known else "uncertain"
        db.execute(
            "INSERT INTO model_budget_reservations VALUES(?,?,?,?,?,?,?,?)",
            (
                rid,
                identity,
                cycle,
                policy.unit,
                amount,
                amount if state == "settled" else None,
                state,
                row["created_at"],
            ),
        )
        db.execute("INSERT INTO model_call_budget_bindings VALUES(?,?)", (row["id"], rid))
    for row in db.execute(
        "SELECT * FROM agent_connection_operations WHERE kind='test_query' AND "
        "json_extract(payload_json,'$.cloud_reservation.credential_id')=? AND "
        "json_extract(payload_json,'$.cloud_reservation.budget_reservation_id') IS NULL",
        (old_id,),
    ).fetchall():
        rid = "historical-agent:" + row["id"]
        if db.execute("SELECT 1 FROM model_budget_reservations WHERE id=?", (rid,)).fetchone():
            continue
        payload = json.loads(row["payload_json"])["cloud_reservation"]
        result = json.loads(row["result_json"]) if row["result_json"] else {}
        usage = result.get("usage") or {}
        known = (
            type(usage.get("prompt_tokens")) is int and type(usage.get("completion_tokens")) is int
        )
        amount = (
            (usage["prompt_tokens"] + usage["completion_tokens"] if known else policy.limit)
            if policy.unit == "tokens"
            else result.get("cloud_cost_nusd", payload["reserved_nusd"])
        )
        cycle = datetime.fromtimestamp(row["created_at"], ZoneInfo(policy.timezone)).strftime(
            "%Y-%m" if policy.period == "month" else "%Y-%m-%d"
        )
        db.execute(
            "INSERT INTO model_budget_reservations VALUES(?,?,?,?,?,NULL,'uncertain',?)",
            (rid, identity, cycle, policy.unit, amount, row["created_at"]),
        )


def reserve(db: sqlite3.Connection, secret_file: str, tokens: int, cost: int) -> str | None:
    identity = key(db, secret_file)
    if identity is None:
        return None
    saved = get(db, identity)
    assert saved is not None
    policy = Policy.model_validate(saved["settings"])
    cycle = period(policy)
    if policy.unit == "USD_nano" and cost == 0:
        raise Exhausted("model_price_unknown_use_token_budget")
    amount = tokens if policy.unit == "tokens" else cost
    if type(amount) is not int or amount < 0:
        raise InvalidInput("budget_reservation_invalid")
    if totals(db, identity, cycle) + amount > policy.limit:
        raise Exhausted("model_key_budget_exhausted")
    rid = str(uuid4())
    db.execute(
        "INSERT INTO model_budget_reservations VALUES(?,?,?,?,?,NULL,'reserved',?)",
        (rid, identity, cycle, policy.unit, amount, time.time()),
    )
    return rid


def settle(db: sqlite3.Connection, identity: str | None, tokens: int, cost: int) -> None:
    if identity is None:
        return
    row = db.execute("SELECT * FROM model_budget_reservations WHERE id=?", (identity,)).fetchone()
    if row is None:
        raise InvalidInput("budget_reservation_unavailable")
    amount = tokens if row["unit"] == "tokens" else cost
    if type(amount) is not int or amount < 0:
        raise InvalidInput("budget_usage_invalid")
    if row["state"] == "settled":
        if row["charged"] != amount:
            raise InvalidInput("budget_usage_changed")
        return
    db.execute(
        "UPDATE model_budget_reservations SET state='settled',charged=? WHERE id=?",
        (amount, identity),
    )
    if amount > row["amount"]:
        raise InvalidInput("provider_exceeded_budget_reservation")
