"""One durable pending/current index update per Vault; never launches work automatically."""

import json
import math
import time
from typing import Any
from uuid import uuid4

from .config import Config, IntegrityError, make_directory
from .inputs import canonical
from .note_changes import identifier
from .note_search_cache import location
from .query_policy_store import write_private
from .query_routing_files import read as private_read


def read(config: Config, vault_id: str) -> dict[str, Any] | None:
    path = location(config, vault_id).with_suffix(".job.json")
    if not path.exists():
        return None
    raw = private_read(path, 4096)
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise IntegrityError("note index update record") from exc
    if (
        not isinstance(value, dict)
        or set(value)
        != {"schema", "vault_id", "id", "state", "reason", "updated_at", "model_calls", "result"}
        or canonical(value).encode() != raw
        or value.get("schema") != "note-index-update/v1"
        or value.get("vault_id") != vault_id
        or value.get("state")
        not in {"pending", "building", "ready", "failed", "interrupted", "stale"}
    ):
        raise IntegrityError("note index update record")
    identifier(value["id"])
    if (
        value["reason"]
        not in {
            "document_publication",
            "explicit_update",
            "completed",
            "index_update_failed",
            "restored_explicit_update_required",
            "source_changed",
        }
        or type(value["updated_at"]) not in {int, float}
        or not math.isfinite(value["updated_at"])
        or value["updated_at"] < 0
        or type(value["model_calls"]) is not int
        or value["model_calls"] != 0
    ):
        raise IntegrityError("note index update record")
    counts = value["result"]
    if counts is not None and (
        not isinstance(counts, dict)
        or set(counts) != {"reused_notes", "updated_notes", "notes", "chunks"}
        or any(type(n) is not int or n < 0 for n in counts.values())
    ):
        raise IntegrityError("note index update record")
    return value


def record(
    config: Config,
    vault_id: str,
    state: str,
    *,
    reason: str,
    result: dict[str, object] | None = None,
    expected_id: str | None = None,
) -> str:
    previous = read(config, vault_id)
    if expected_id is not None and (previous is None or previous["id"] != expected_id):
        return previous["id"] if previous else expected_id
    value = {
        "schema": "note-index-update/v1",
        "vault_id": vault_id,
        "id": previous["id"]
        if state != "pending" and previous and previous["state"] in {"pending", "building"}
        else str(uuid4()),
        "state": state,
        "reason": reason,
        "updated_at": time.time(),
        "model_calls": 0,
        "result": result,
    }
    path = location(config, vault_id).with_suffix(".job.json")
    make_directory(path.parent)
    write_private(path, canonical(value).encode())
    return str(value["id"])


def changed(config: Config, vault_id: str) -> None:
    """Coalesce a detected edit/delete/move; never run extraction or rebuild implicitly."""
    previous = read(config, vault_id)
    if previous is None or previous["state"] not in {"pending", "building"}:
        record(config, vault_id, "pending", reason="source_changed")
