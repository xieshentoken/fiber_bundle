"""Durable stop/unbind/remove intents and legacy tombstones; no model or network calls."""

import json
import sqlite3
import time
from typing import Any
from uuid import UUID

from .. import auth
from ..config import Config, InvalidInput
from ..inputs import canonical
from . import legacy, store


def operation(row: sqlite3.Row) -> dict[str, Any]:
    return {**dict(row), "value": json.loads(row["value"])}


def latest(db: sqlite3.Connection | None, identity: str) -> dict[str, Any] | None:
    if (
        db is None
        or not db.execute("SELECT 1 FROM sqlite_master WHERE name='channel_operations'").fetchone()
    ):
        return None
    row = db.execute(
        "SELECT * FROM channel_operations WHERE channel_id=? "
        "ORDER BY created DESC,rowid DESC LIMIT 1",
        (identity,),
    ).fetchone()
    return operation(row) if row else None


def pending(db: sqlite3.Connection, identity: str) -> bool:
    value = latest(db, identity)
    return bool(value and value["state"] in {"pending", "needs_review"})


def get(db: sqlite3.Connection, operation_id: str) -> dict[str, Any]:
    try:
        if str(UUID(operation_id)) != operation_id:
            raise ValueError()
    except ValueError:
        raise InvalidInput("channel_identifier_invalid") from None
    row = db.execute("SELECT * FROM channel_operations WHERE id=?", (operation_id,)).fetchone()
    if row is None:
        raise auth.Denied(404)
    return operation(row)


def public(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    result = {key: value[key] for key in ("id", "action", "state", "created", "updated", "reason")}
    if result["state"] == "pending" and time.time() - result["updated"] > 75:
        result.update(state="needs_review", reason="channel_operation_interrupted")
    return result


def legacy_overlay(
    db: sqlite3.Connection | None, values: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_id = {entry["id"]: entry for entry in values}
    if (
        db is not None
        and db.execute("SELECT 1 FROM sqlite_master WHERE name='legacy_channels'").fetchone()
    ):
        for row in db.execute("SELECT * FROM legacy_channels ORDER BY rowid"):
            saved = json.loads(row["value"])
            current = by_id.get(row["id"], saved)
            current.update(revision=row["revision"], state=row["state"])
            by_id[row["id"]] = current
    return list(by_id.values())


def entry(config: Config, db: sqlite3.Connection, identity: str) -> dict[str, Any]:
    try:
        return store.get(db, identity)
    except auth.Denied:
        values, _ = legacy.entries(config)
        found = next((item for item in legacy_overlay(db, values) if item["id"] == identity), None)
        if found is None:
            raise auth.Denied(404) from None
        return found


def set_entry_state(db: sqlite3.Connection, channel: dict[str, Any], state: str) -> None:
    if channel.get("origin") != "legacy":
        store.set_state(db, channel["id"], state)
    else:
        db.execute(
            "INSERT INTO legacy_channels VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "revision=excluded.revision,value=excluded.value,state=excluded.state",
            (channel["id"], channel["revision"], canonical(channel), state),
        )


def update(
    db: sqlite3.Connection, value: dict[str, Any], state: str, reason: str | None = None
) -> None:
    db.execute(
        "UPDATE channel_operations SET state=?,value=?,updated=?,reason=? WHERE id=?",
        (state, canonical(value["value"]), time.time(), reason, value["id"]),
    )
