"""Stop only the resolved intake scope; preserve committed notes and billing history."""

import json
from typing import Any
from uuid import UUID

from . import (
    automatic_jobs,
    database,
    execution,
    execution_ledger,
    intake_abort_scope,
    weixin_inbox,
)
from .channels import store
from .channels.intake_processing import record
from .config import Config
from .weixin_events import Binding


def run_ids(value: Any) -> set[str]:
    found: set[str] = set()
    pending = [value]
    for _ in range(10000):
        if not pending:
            return found
        item = pending.pop()
        if isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, str):
            try:
                if str(UUID(item)) == item:
                    found.add(item)
            except ValueError:
                pass
    return found


def matches(scope: dict[str, Any], job: dict[str, Any], legacy: set[str]) -> bool:
    if scope["kind"] == "job":
        return bool(scope["job_id"] == job["id"])
    context = intake_abort_scope.job_context(job)
    if (context.get("channel"), context.get("batch")) != (scope["channel"], scope["batch"]):
        return scope["kind"] == "batch" and job["id"] in legacy
    return bool(scope["kind"] == "batch" or context.get("index") == scope["index"])


def stop(config: Config, binding: Binding, scope: dict[str, Any]) -> dict[str, Any]:
    stopped_runs: set[str] = set()
    cancelled = preserved = 0
    with (
        database.locked(config),
        database.connect(config.state_root) as db,
        store.opened(config) as side,
    ):
        row = side.execute(
            "SELECT * FROM channel_intake_records WHERE channel_id=? AND batch_id=?",
            (scope["channel"], scope.get("batch")),
        ).fetchone()
        value = json.loads(row["value"]) if row else {}
        if scope["kind"] != "batch" and intake_abort_scope.committed(db, scope, value or None):
            return {"state": "too_late", "scope": scope}
        legacy = {i["job_id"] for i in value.get("automatic_imports", []) if i.get("job_id")}
        jobs = [
            {**dict(r), "value": json.loads(r["value"])}
            for r in db.execute(
                "SELECT * FROM automatic_jobs WHERE client_id=?", (binding.client_id,)
            )
        ]
        for job in jobs:
            if not matches(scope, job, legacy):
                continue
            if intake_abort_scope.committed(db, {"kind": "job", "job_id": job["id"]}):
                preserved += 1
                continue
            automatic_jobs.settle(db, job, "cancelled", reason="intake_user_aborted")
            stopped_runs.update(run_ids(job["value"]))
            if job["kind"] == "document" and not job["value"].get("chat_archive"):
                source = db.execute(
                    "SELECT run_id FROM source_imports WHERE id=?", (job["source_id"],)
                ).fetchone()
                if source and source[0]:
                    stopped_runs.add(source[0])
            elif job["kind"] == "expert":
                from .expert_tasks import cancel

                cancel(db, binding.client_id, job["source_id"])
                for operation in db.execute(
                    "SELECT payload FROM vault_operations WHERE kind='expert_authoring' "
                    "AND json_extract(payload,'$.import_id')=?",
                    (job["source_id"],),
                ):
                    stopped_runs.update(run_ids(json.loads(operation[0])))
            cancelled += 1
        if row and scope["kind"] in {"batch", "link"}:
            for index, link in enumerate(value.get("links", [])):
                if scope["kind"] == "link" and index != scope["index"]:
                    continue
                if intake_abort_scope.committed(
                    db,
                    {
                        "kind": "link",
                        "channel": scope["channel"],
                        "batch": scope["batch"],
                        "index": index,
                    },
                    value,
                ):
                    preserved += 1
                    continue
                stopped_runs.update(run_ids(link.get("pi_processing", {})))
                stopped_runs.update(run_ids(link.get("video_processing", {})))
                link.update(state="cancelled", aborted=True, reason="intake_user_aborted")
                for name in ("pi_processing", "video_processing"):
                    if link.get(name):
                        link[name]["state"] = "cancelled"
                cancelled += 1
            if not cancelled and preserved:
                return {"state": "too_late", "scope": scope}
            if scope["kind"] == "batch":
                value.update(aborted=True, extraction="cancelled")
            with database.transaction(side):
                record(
                    side,
                    scope["channel"],
                    scope["batch"],
                    row["revision"],
                    row["state"] if row["state"] == "stored" else "manual",
                    "intake_user_aborted",
                    value,
                )
        for rid in list(stopped_runs):
            run = db.execute(
                "SELECT * FROM execution_runs WHERE id=? AND client_id=?", (rid, binding.client_id)
            ).fetchone()
            if not run or run["state"] not in {
                "queued",
                "running",
                "waiting_budget",
                "interrupted",
            }:
                stopped_runs.discard(rid)
                continue
            execution_ledger.transition(db, rid, "cancelled", "intake_user_aborted")
    if scope["kind"] == "batch":
        with weixin_inbox.connection(config, binding) as journal, database.transaction(journal):
            journal.execute(
                "UPDATE wx_batches SET state='cancelled',action='cancel' "
                "WHERE id=? AND account_id=?",
                (scope["batch"], binding.account_id),
            )
    pending = 0
    for rid in stopped_runs:
        try:
            execution.cancel(config, rid)
        except Exception:
            # The durable cancellation already prevents all later publication.
            # A stuck external request may finish and settle its existing bill.
            pending += 1
    return {
        "state": "stopped",
        "scope": scope,
        "cancelled": cancelled,
        "preserved_publications": preserved,
        "pending_resource_release": pending,
        "originals_deleted": False,
        "existing_costs_preserved": True,
    }
