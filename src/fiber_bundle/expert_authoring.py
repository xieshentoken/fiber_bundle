"""Persisted chapter proposal workflow, retaining compiler and independent-review boundaries."""

import json
import sqlite3
import time
from typing import Any
from uuid import uuid4

from . import automatic_policy, expert_tasks, source_imports
from .config import Config, InvalidInput
from .inputs import canonical


def lookup(db: sqlite3.Connection, identity: str) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT * FROM vault_operations WHERE kind='expert_authoring' "
        "AND json_extract(payload,'$.import_id')=? ORDER BY created_at DESC LIMIT 1",
        (identity,),
    ).fetchone()
    return {**dict(row), "value": json.loads(row["payload"])} if row else None


def authorize(config: Config, db: sqlite3.Connection, row: dict[str, Any]) -> tuple[Any, Any, Any]:
    value = row["value"]
    source, plan, file = expert_tasks.authorize(config, db, value["client_id"], value["import_id"])
    if (
        automatic_policy.get(db, source["vault_id"]) != value["vault_policy"]
        or source_imports.epoch(db, value["client_id"], source["vault_id"]) != value["epoch"]
        or plan["plan_sha256"] != value["plan_sha256"]
    ):
        raise InvalidInput("expert_authoring_binding_changed")
    model = automatic_policy.processing_route(config, value["vault_policy"]["settings"])
    if model.digest != value["model_sha256"]:
        raise InvalidInput("expert_authoring_binding_changed")
    return source, plan, file


def save(db: sqlite3.Connection, row: dict[str, Any], state: str) -> None:
    db.execute(
        "UPDATE vault_operations SET state=?,payload=?,updated_at=? WHERE id=?",
        (state, canonical(row["value"]), time.time(), row["id"]),
    )


def prepare(config: Config, db: sqlite3.Connection, cid: str, identity: str) -> dict[str, Any]:
    old = lookup(db, identity)
    if old:
        authorize(config, db, old)
        return old
    source, plan, _ = expert_tasks.authorize(config, db, cid, identity)
    policy = automatic_policy.get(db, source["vault_id"])
    if (
        policy["settings"].get("processing_mode") != "pi_skill"
        or policy["settings"].get("client_id") != cid
        or not automatic_policy.active(policy["settings"])
    ):
        raise InvalidInput("expert_authoring_policy_required")
    model = automatic_policy.processing_route(config, policy["settings"])
    from .expert_tool_versions import LEGACY, verify

    value = {
        "import_id": identity,
        "client_id": cid,
        "vault_policy": policy,
        "model_sha256": model.digest,
        "epoch": source_imports.epoch(db, cid, source["vault_id"]),
        "plan_sha256": plan["plan_sha256"],
        "tool_sha256": plan.get("project_skill", {}).get("host_tools_sha256") or LEGACY,
        "units": {},
        "known_gaps": [],
        "knowledge_verified": False,
    }
    verify(config, value["tool_sha256"])
    if plan.get("project_skill"):
        value["project_skill"] = plan["project_skill"]
    operation, now = str(uuid4()), time.time()
    db.execute(
        "INSERT INTO vault_operations VALUES(?,?,'expert_authoring','queued',?,?,?)",
        (operation, source["vault_id"], canonical(value), now, now),
    )
    created = lookup(db, identity)
    assert created is not None
    return created


def needed(config: Config, db: sqlite3.Connection, cid: str, identity: str) -> bool:
    source = source_imports.get(config, db, cid, identity)
    settings = automatic_policy.get(db, source["vault_id"])["settings"]
    task = expert_tasks.get(db, identity)
    current = lookup(db, identity)
    return bool(
        task
        and task["state"] in {"prepared", "paused"}
        and settings.get("processing_mode") == "pi_skill"
        and automatic_policy.active(settings)
        and not (current and current["state"] == "complete")
    )


def projection(db: sqlite3.Connection, identity: str) -> dict[str, Any] | None:
    row = lookup(db, identity)
    if row is None:
        return None
    value = row["value"]
    from .processing_errors import MESSAGES

    return {
        "state": row["state"],
        "operation_id": row["id"],
        "completed_fragments": sum(
            p.get("state") == "complete"
            for u in value["units"].values()
            for p in u.get("parts", {}).values()
        ),
        "completed_units": sum(u.get("state") == "complete" for u in value["units"].values()),
        "total_units": value.get("total_units"),
        "known_gaps": value["known_gaps"],
        "code": value.get("error_code"),
        "message": MESSAGES.get(value.get("error_code")),
        "workpack_import_id": value.get("workpack_import_id"),
        "knowledge_verified": False,
    }
