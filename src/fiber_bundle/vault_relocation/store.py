"""Durable migration intents and public status; historical records are never rewritten."""

import hashlib
import json
import time
from typing import Any

from ..inputs import canonical
from .errors import RelocationError


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def load(db: Any, identity: str) -> tuple[dict[str, Any], str]:
    row = db.execute(
        "SELECT * FROM vault_operations WHERE kind='vault_relocation' AND id=?", (identity,)
    ).fetchone()
    if row is None:
        raise RelocationError("relocation_unavailable", 404)
    return json.loads(row["payload"]), row["state"]


def save(db: Any, identity: str, value: dict[str, Any], state: str) -> None:
    db.execute(
        "UPDATE vault_operations SET payload=?,state=?,updated_at=? WHERE id=?",
        (canonical(value), state, time.time(), identity),
    )


def view(db: Any, identity: str) -> dict[str, Any]:
    value, state = load(db, identity)
    p = value["plan"]
    return {
        "id": identity,
        "vault_id": p["vault"]["id"],
        "name": p["vault"]["name"],
        "state": state,
        "phase": value.get("phase", state),
        "reason": value.get("reason"),
        "source": p["source"],
        "destination": p["destination"],
        "plan_sha256": value["plan_sha256"],
        "expires_at": p["expires_at"],
        "files": sum(f["kind"] == "file" for f in p["files"]),
        "bytes": sum(f.get("bytes", 0) for f in p["files"]),
        "preservation": p["summary"],
        "checkpoint": value.get("checkpoint"),
        "completed_phases": value.get("completed_phases", []),
        "current_path": value.get("current_path", p["source"]),
        "location_revision": value.get("location_revision", 0),
        "recovery": value.get("recovery"),
        "model_calls": 0,
    }
