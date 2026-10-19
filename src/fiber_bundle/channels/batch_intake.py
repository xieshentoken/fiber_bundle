"""Bridge admin batch choices to the existing channel queue and its read-only progress."""

import json
import sqlite3
import time
from typing import Any

from ..config import Config, InvalidInput, StorageError
from ..query_routing_files import read
from ..weixin_events import Binding
from . import intake_actions, intake_contract, intake_repository, legacy, operations, store


def channel_for(config: Config, bound: Binding) -> dict[str, Any] | None:
    if not bound.account_id.startswith("feishu:"):
        return None
    values, _ = legacy.entries(config)
    with store.readonly(config) as side:
        values = operations.legacy_overlay(side, values)
        if side is not None:
            values += [
                store.get(side, r[0])
                for r in side.execute("SELECT id FROM channels WHERE state!='removed' LIMIT 101")
            ]
    matches = [
        c
        for c in values
        if c["state"] != "removed"
        and c["value"].get("platform", "feishu") == "feishu"
        and (c.get("origin") == "legacy" or c["value"].get("add"))
        and c.get("owner")
        and intake_repository.binding(c) == bound
    ]
    if len(matches) > 1:
        raise InvalidInput("intake_channel_ambiguous")
    return matches[0] if matches else None


def enqueue(config: Config, bound: Binding, batch: str, choice: str) -> dict[str, Any] | None:
    """HTTP Guard owns the storage lease. Repeated requests retain the same queue identity."""
    if choice not in {"save_one", "save_separately", "cancel"}:
        return None
    channel = channel_for(config, bound)
    if channel is None:
        if bound.account_id.startswith("feishu:") and choice != "cancel":
            raise InvalidInput("intake_channel_unavailable")
        return None
    _, policy = intake_repository.channel(config, channel["id"])
    if choice == "cancel":
        return (
            intake_actions.cancel(
                config,
                channel["id"],
                intake_contract.BatchAction(
                    expected_revision=policy["revision"], batch_ids=[batch]
                ),
                leased=True,
            )
            if policy["revision"]
            else None
        )
    if not policy["revision"] or not policy["settings"]["target_vault_id"]:
        raise InvalidInput("intake_target_required")
    if channel["state"] != "running":
        raise InvalidInput("intake_channel_paused")
    return intake_actions.select(
        config,
        channel["id"],
        intake_contract.BatchAction(expected_revision=policy["revision"], batch_ids=[batch]),
        leased=True,
        choice=choice,
    )


def worker_status(config: Config, channel: dict[str, Any]) -> dict[str, Any]:
    home = (
        legacy.home(config, channel)
        if channel.get("origin") == "legacy"
        else store.home(config, channel["id"])
    )
    path = home / "intake-status.json"
    try:
        value = json.loads(read(path, 4096))
        runtime = (
            json.loads(read(home / "fiber-status.json", 4096))
            if (home / "fiber-status.json").exists()
            else {}
        )
        observed = value.get("observed_at", 0)
        fresh = (
            type(observed) in {int, float}
            and 0 <= time.time() - observed <= 30
            and observed >= runtime.get("started_at", 0)
        )
    except (OSError, ValueError, StorageError, AttributeError, TypeError):
        return {"state": "unobserved", "error": None, "fresh": False, "observed_at": None}
    states = {
        "ready",
        "processing",
        "off",
        "unconfigured",
        "needs_review",
        "waiting_storage",
        "intake_requires_review",
        "intake_permission_unavailable",
    }
    return {
        "state": value.get("state") if value.get("state") in states else "unobserved",
        "fresh": fresh,
        "observed_at": observed,
        "error": value.get("error")
        if value.get("error")
        in {
            "intake_storage_busy",
            "channel_connection_timeout",
            "channel_storage_failed",
            "channel_local_io_failed",
            "channel_runtime_validation_failed",
            "channel_internal_failed",
        }
        else None,
    }


def progress(
    config: Config, main: sqlite3.Connection, bound: Binding, batch: str
) -> dict[str, Any] | None:
    channel = channel_for(config, bound)
    if channel is None:
        return None
    with store.readonly(config) as side:
        row = (
            side.execute(
                "SELECT * FROM channel_intake_records WHERE channel_id=? AND batch_id=?",
                (channel["id"], batch),
            ).fetchone()
            if side is not None and intake_repository.existing(side)
            else None
        )
    value = json.loads(row["value"]) if row else {}
    from .. import automatic_policy
    from .intake_recovery_status import project as recovery_status

    target = (
        value.get("target_vault_id")
        or intake_repository.channel(config, channel["id"])[1]["settings"]["target_vault_id"]
        or bound.vault_id
    )
    settings = automatic_policy.get(main, target)["settings"]
    imports = [item["job_id"] for item in value.get("automatic_imports", []) if item.get("job_id")]
    placeholders = ",".join("?" for _ in imports[:100]) or "NULL"
    jobs = [
        dict(r)
        for r in main.execute(
            "SELECT id,state,phase,reason,"
            "coalesce(json_array_length(value,'$.missing_images'),0)>0 AS missing_images "
            "FROM automatic_jobs AS j "
            "WHERE (json_extract(value,'$.intake.batch')=? "
            "OR json_extract(value,'$.channel_batch.batch')=? OR id IN ("
            + placeholders
            + ")) AND NOT EXISTS (SELECT 1 FROM automatic_jobs AS successor "
            "WHERE json_extract(successor.value,'$.supersedes_job')=j.id) ORDER BY created_at",
            (batch, batch, *imports[:100]),
        )
    ]
    from ..model_configuration.errors import MESSAGES as model_messages
    from ..processing_errors import MESSAGES as processing_messages

    reasons = [row["reason"] if row else None] + [
        item.get("reason")
        for item in [*value.get("links", []), *jobs, *value.get("automatic_imports", [])]
    ]
    messages = {
        code: model_messages[code][1] if code in model_messages else processing_messages[code]
        for code in reasons
        if code in model_messages or code in processing_messages
    }
    return {
        "channel_id": channel["id"],
        "state": row["state"] if row else "not_queued",
        "reason": row["reason"] if row else None,
        "cancelled": bool(value.get("aborted")),
        "messages": messages,
        "source_failures": [
            item["reason"] for item in value.get("automatic_imports", []) if item.get("reason")
        ],
        "worker": worker_status(config, channel),
        "automatic_save": automatic_policy.active(settings)
        and settings.get("client_id") == bound.client_id,
        "links": [
            {
                "state": link.get("state", "pending"),
                "reason": link.get("reason"),
                "published": bool(link.get("publication")),
                "model_state": recovery_status(main, link)["model_state"],
                "processor": "bili-note-V3" if link.get("video_processing") else "literal",
                "phase": link.get("video_processing", {}).get("phase"),
                "task_id": link.get("video_processing", {}).get("job_id"),
            }
            for link in value.get("links", [])
        ],
        "jobs": jobs,
    }
