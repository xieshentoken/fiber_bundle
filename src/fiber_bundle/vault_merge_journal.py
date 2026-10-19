"""Durable merge stage/progress records in the existing private operation ledger."""

import json
import time
from typing import Any

from .config import InvalidInput
from .inputs import canonical


def load(db: Any, identity: str) -> tuple[dict[str, Any], str]:
    row = db.execute(
        "SELECT * FROM vault_operations WHERE id=? AND kind='vault_merge'", (identity,)
    ).fetchone()
    if row is None:
        raise InvalidInput("vault_merge_unavailable")
    return json.loads(row["payload"]), row["state"]


def save(db: Any, identity: str, value: dict[str, Any], state: str) -> None:
    db.execute(
        "UPDATE vault_operations SET state=?,payload=?,updated_at=? WHERE id=?",
        (state, canonical(value), time.time(), identity),
    )


def view(db: Any, identity: str) -> dict[str, Any]:
    value, state = load(db, identity)
    return {
        "id": identity,
        "state": state,
        "phase": value.get("phase", state),
        "reason": value.get("reason"),
        "files": len(value["plan"]["files"]),
        "copied": sum(p.get("state") == "copied" for p in value["progress"].values()),
        "source_files_retained": True,
        "backup_verified": bool(value.get("backup")),
        "cancel_requested": bool(value.get("cancel_requested")),
        "rollback_started": bool(value.get("rollback_started")),
        "source_vault_ids": value["plan"]["selection"]["source_vault_ids"],
        "target_vault_id": value["plan"]["selection"]["target_vault_id"],
        "plan_sha256": value["plan"]["plan_sha256"],
    }
