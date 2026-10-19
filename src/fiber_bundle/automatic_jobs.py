"""Immutable automatic admission, atomic claims and observable, bounded exception queue."""

import json
import sqlite3
import time
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from . import automatic_policy
from .config import Config, InvalidInput
from .inputs import canonical


def enqueue(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vault: str,
    kind: str,
    source: str,
    payload: dict[str, Any] | None = None,
) -> str | None:
    frozen = automatic_policy.binding(
        config, db, vault, client, "intake" if kind == "intake_summary" else kind
    )
    if frozen is None:
        return None
    identity = str(uuid5(NAMESPACE_URL, "fiber-auto:" + kind + ":" + source))
    now = time.time()
    db.execute(
        "INSERT OR IGNORE INTO automatic_jobs VALUES(?,?,?,?,?,?,?,'queued',?,NULL,?,?)",
        (
            identity,
            vault,
            client,
            kind,
            source,
            frozen["policy_revision"],
            "extract" if kind in {"document", "expert", "intake_summary"} else "review",
            canonical({"binding": frozen, **(payload or {})}),
            now,
            now,
        ),
    )
    return identity


def get(db: sqlite3.Connection, identity: str) -> dict[str, Any]:
    row = db.execute("SELECT * FROM automatic_jobs WHERE id=?", (identity,)).fetchone()
    if row is None:
        raise InvalidInput("automatic_job_unavailable")
    return {**dict(row), "value": json.loads(row["value"])}


def authorize(config: Config, db: sqlite3.Connection, job: dict[str, Any]) -> dict[str, Any]:
    current = get(db, job["id"])
    binding = automatic_policy.binding(
        config,
        db,
        job["vault_id"],
        job["client_id"],
        "intake" if job["kind"] == "intake_summary" else job["kind"],
    )
    frozen_binding = job["value"]["binding"]
    if job["kind"] in {"intake", "intake_summary"}:
        # Old captures froze an optional analysis model as a mandatory source
        # dependency. Source authorization no longer depends on that model.
        frozen_binding = {k: v for k, v in frozen_binding.items() if k != "processing_model_sha256"}
    if current["state"] == "cancelled" or binding != frozen_binding:
        raise InvalidInput("automatic_authorization_changed")
    assert binding is not None
    from .intake_abort_publication import guard_job

    guard_job(config, job)
    return binding


def settle(
    db: sqlite3.Connection,
    job: dict[str, Any],
    state: str,
    phase: str | None = None,
    reason: str | None = None,
) -> None:
    if job["value"].get("pending_model") and state in {"queued", "reviewed"}:
        state, phase = "queued", "model_review"
    db.execute(
        "UPDATE automatic_jobs SET state=?,phase=?,value=?,reason=?,updated_at=? WHERE id=? "
        "AND state!='cancelled'",
        (state, phase or job["phase"], canonical(job["value"]), reason, time.time(), job["id"]),
    )


def claim(
    db: sqlite3.Connection,
    *,
    channel_id: str | None = None,
    admit_after: float = 0,
    excluded_channels: set[str] | None = None,
) -> dict[str, Any] | None:
    from .vault_relocation.guard import operation

    def eligible(row: Any) -> bool:
        value = json.loads(row["value"])
        owner = value.get("intake", {}).get("channel") or value.get("channel_batch", {}).get(
            "channel"
        )
        if channel_id is not None:
            return owner == channel_id and row["created_at"] >= admit_after
        return owner not in (excluded_channels or set())

    row = next(
        (
            r
            for r in db.execute(
                "SELECT id,vault_id,value,created_at FROM automatic_jobs "
                "WHERE state IN ('queued','waiting') "
                "ORDER BY updated_at,id"
            )
            if eligible(r) and operation(db, r["vault_id"]) is None
        ),
        None,
    )
    if row is None:
        return None
    updated = db.execute(
        "UPDATE automatic_jobs SET state='claimed',updated_at=? WHERE id=? "
        "AND state IN ('queued','waiting')",
        (time.time(), row[0]),
    )
    return get(db, row[0]) if updated.rowcount == 1 else None


def listing(db: sqlite3.Connection, vault: str) -> list[dict[str, Any]]:
    from .automatic_job_status import project
    from .channels.intake_recovery_status import project as recovery_status

    return [
        {
            key: job[key]
            for key in [
                "id",
                "client_id",
                "kind",
                "source_id",
                "phase",
                "state",
                "reason",
                "updated_at",
                "policy_revision",
            ]
        }
        | {
            "requires_new_model_attempt": (
                recovery_status(db, {"pi_processing": job["value"]})["requires_new_model_attempt"]
                if job["kind"] == "intake_summary"
                else False
            ),
        }
        | project(db, job)
        for row in db.execute(
            "SELECT id FROM automatic_jobs WHERE vault_id=? ORDER BY updated_at DESC LIMIT 100",
            (vault,),
        )
        for job in [get(db, row[0])]
    ]


def display_state(job: dict[str, Any]) -> str:
    from .automatic_job_status import display_state as describe

    return describe(job)
