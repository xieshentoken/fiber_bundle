"""Persistent per-entry service intent; enabling never replays historical work."""

import json
import sqlite3
import time
from typing import Any, Literal

from ...config import Config, InvalidInput
from ...inputs import canonical
from .. import operations, store

Role = Literal["query", "intake"]
ROLES = ("query", "intake")


def schema(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS channel_services (
        channel_id TEXT NOT NULL, role TEXT NOT NULL, revision INTEGER NOT NULL,
        value TEXT NOT NULL, PRIMARY KEY(channel_id,role)) STRICT""")


def roles(channel: dict[str, Any]) -> list[str]:
    value = channel["value"]
    purpose = value.get("purpose")
    if purpose == "query":
        return ["query"]
    if purpose == "intake":
        return ["intake"]
    return (
        ["query", "intake"]
        if value.get("add") and value.get("platform", "feishu") == "feishu"
        else ["query"]
    )


def get(config: Config, identity: str, role: str) -> dict[str, Any] | None:
    if role not in ROLES:
        raise InvalidInput("channel_service_role_invalid")
    with store.readonly(config) as db:
        if (
            db is None
            or not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='channel_services'"
            ).fetchone()
        ):
            return None
        row = db.execute(
            "SELECT revision,value FROM channel_services WHERE channel_id=? AND role=?",
            (identity, role),
        ).fetchone()
    return {**json.loads(row["value"]), "revision": row["revision"]} if row else None


def managed(config: Config, identity: str) -> bool:
    return any(get(config, identity, role) is not None for role in ROLES)


def cutoff(config: Config, identity: str, role: str = "intake") -> float:
    value = get(config, identity, role)
    return value["admit_after"] if value else 0


def enabled(config: Config, identity: str, role: str) -> bool:
    value = get(config, identity, role)
    return bool(value and value["enabled"])


def change(
    config: Config,
    identity: str,
    role: str,
    expected: int,
    *,
    enabled: bool,
    runtime_image: str | None = None,
) -> dict[str, Any]:
    """Caller owns the storage lease. Role pause is separate from transport state."""
    if role not in ROLES or type(enabled) is not bool:
        raise InvalidInput("channel_service_request_invalid")
    with store.opened(config) as db:
        channel = operations.entry(config, db, identity)
        if role not in roles(channel):
            raise InvalidInput("channel_service_role_unavailable")
        if channel["state"] in {"removed", "unbinding", "removing"} or not channel["owner"]:
            raise InvalidInput("channel_service_binding_unavailable")
        if operations.pending(db, identity):
            raise InvalidInput("channel_operation_pending")
        schema(db)
        prior = get(config, identity, role)
        if expected != (prior["revision"] if prior else 0):
            raise InvalidInput("channel_service_revision_conflict")
        value = {k: v for k, v in (prior or {}).items() if k != "revision"}
        if prior is None:
            value.update(admit_after=time.time(), source_watermark=0, expert_watermark=0)
            journal = config.state_root / "weixin-inbox.sqlite3"
            if role == "intake" and journal.exists():
                from ..intake_repository import binding

                with sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True) as inbox:
                    if inbox.execute(
                        "SELECT 1 FROM sqlite_master WHERE name='fiber_expert_commands'"
                    ).fetchone():
                        value["expert_watermark"] = inbox.execute(
                            "SELECT coalesce(max(rowid),0) FROM fiber_expert_commands"
                        ).fetchone()[0]
                    value["source_watermark"] = inbox.execute(
                        "SELECT coalesce(max(rowid),0) FROM events WHERE account_id=?",
                        (binding(channel).account_id,),
                    ).fetchone()[0]
        value.update(enabled=enabled, updated_at=time.time())
        if runtime_image is not None:
            value["runtime_image"] = runtime_image
        db.execute(
            "INSERT INTO channel_services VALUES(?,?,?,?) ON CONFLICT(channel_id,role) "
            "DO UPDATE SET revision=excluded.revision,value=excluded.value",
            (identity, role, expected + 1, canonical(value)),
        )
    return {**value, "revision": expected + 1}


def owned_channels(config: Config) -> set[str]:
    with store.readonly(config) as db:
        if (
            db is None
            or not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='channel_services'"
            ).fetchone()
        ):
            return set()
        return {
            r[0] for r in db.execute("SELECT channel_id FROM channel_services WHERE role='intake'")
        }


def intake_allowed(config: Config, channel: dict[str, Any]) -> bool:
    """Stopping reception is not revoking processing permission."""
    if channel["value"].get("purpose") == "query":
        return False
    service = get(config, channel["id"], "intake")
    if service is None:
        return bool(channel["state"] == "running")
    # Intent stops new claims; an already-started phase may finish at its safe boundary.
    # Authorization and destructive channel operations are still checked below.
    return channel["state"] in {"running", "paused", "stopping", "error"}
