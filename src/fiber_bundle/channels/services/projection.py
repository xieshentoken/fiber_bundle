"""Read-only GUI projection of bot purpose and each independent worker."""

from typing import Any

from ...config import Config
from .. import store
from . import control, policy


def project(config: Config, channel: dict[str, Any]) -> dict[str, Any]:
    roles = policy.roles(channel)
    result: dict[str, Any] = {
        "purpose": channel["value"].get("purpose") or ("both" if len(roles) > 1 else roles[0]),
        "supported": channel.get("origin") != "legacy",
        "roles": {},
    }
    for role in roles:
        state = control.status(config, channel["id"], role)
        state["held_history"] = 0
        if state["managed"]:
            with store.readonly(config) as db:
                if db is None:
                    continue
                if role == "query":
                    state["held_history"] = db.execute(
                        "SELECT count(*) FROM events WHERE channel_id=? AND created<? "
                        "AND state IN ('captured','processing','ready','failed')",
                        (channel["id"], state["admit_after"]),
                    ).fetchone()[0]
                else:
                    state["held_history"] = db.execute(
                        "SELECT count(*) FROM channel_intake_records "
                        "WHERE channel_id=? AND created<? "
                        "AND (state IN ('waiting','queued','archiving','needs_review') "
                        "OR json_extract(value,'$.extraction')='pending')",
                        (channel["id"], state["admit_after"]),
                    ).fetchone()[0]
        result["roles"][role] = state
    return result
