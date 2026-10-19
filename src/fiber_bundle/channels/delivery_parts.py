"""Per-message attempt receipts inside the existing event; no payloads or replay queue."""

import json
from typing import Any

from .. import database
from ..config import Config, InvalidInput
from ..inputs import canonical
from . import store


def attach(config: Config, identity: str, event_id: str, guard: Any) -> None:
    def update(key: str, kind: str, state: str, reason: str = "") -> None:
        with database.locked(config), store.opened(config) as db:
            row = db.execute(
                "SELECT state,value FROM events WHERE channel_id=? AND event_id=?",
                (identity, event_id),
            ).fetchone()
            if row is None or row["state"] != "sending":
                raise InvalidInput("channel_send_withdrawn")
            value = json.loads(row["value"])
            parts = value.setdefault("delivery_parts", {})
            prior = parts.get(key)
            if state == "sending":
                if prior is not None:
                    raise InvalidInput("channel_send_replay_forbidden")
            elif prior is None or prior["state"] != "sending" or prior["kind"] != kind:
                raise InvalidInput("channel_delivery_receipt_conflict")
            parts[key] = {"kind": kind, "state": state, "reason": reason}
            db.execute(
                "UPDATE events SET value=? WHERE channel_id=? AND event_id=?",
                (canonical(value), identity, event_id),
            )

    guard.part = update


def public(value: str) -> list[dict[str, str]]:
    parts = json.loads(value).get("delivery_parts", {})
    return [{"part": key, **item} for key, item in parts.items()]
