"""Mode tasks share the existing private queue; no application before bound confirmation."""

import time
from typing import Any
from uuid import uuid4

from ..config import IntegrityError, InvalidInput
from . import maintenance_plan as plans
from . import manual_resolution, packages, store


def submit(
    config: Any,
    db: Any,
    client: str,
    vault: str,
    mode: str,
    new_mocs: Any = None,
    *,
    recovery: Any = None,
) -> Any:
    es, ms, fixed = plans.snapshot(config, db, client, vault, mode)
    fresh = plans.virtual_mocs(vault, fixed["save_policy"]["settings"], ms, new_mocs or [])
    context = plans.plan_context(config, vault, ms + fresh)
    root, allowed = packages.context(config, db, client, vault, write=True)
    for m in fresh:
        if not allowed.allows(m["path"]) or (root / m["path"]).exists():
            raise IntegrityError("maintenance_moc_target_exists_or_denied")
    job = {
        "id": str(uuid4()),
        "maintenance": plans.VERSION,
        "mode": mode,
        "client_id": client,
        "vault_id": vault,
        "target": "Inbox" if mode == "quick" else "全库",
        "source": "manual",
        "state": "queued",
        "stage": "planning",
        "created_at": time.time(),
        "fixed": fixed,
        "items": [
            plans.item(config, vault, e, ms + fresh, fixed, mode, context=context) for e in es
        ],
        "new_mocs": fresh,
        "mocs": ms + fresh,
        "completed": 0,
        "model_called": False,
        "error": None,
        "permission_epoch": fixed["permission_epoch"],
        "policy": fixed["policy"],
        "confirmed": False,
    }
    from ..inputs import canonical
    from ..objects import digest

    if recovery:
        job["cache_recovery"] = recovery
        job["items"] = [i for i in job["items"] if i["entity"] in recovery["entities"]]
        for item in job["items"]:
            item.update(evaluated=False, phase="analysis", selected=[])
            plans.difference(item, mode, fixed["save_policy"]["settings"])

    job["request_key"] = digest(
        canonical(
            [
                mode,
                client,
                vault,
                fixed,
                [[i["entity"], i["fingerprint"]] for i in job["items"]],
                fresh,
                *([recovery] if recovery else []),
            ]
        ).encode()
    )
    for prior in store.rows(config, "jobs", vault):
        if prior.get("request_key") == job["request_key"] and prior["state"] in {
            "queued",
            "running",
            "preview",
            "blocked",
            "paused",
        }:
            try:
                validate(config, db, prior)
            except IntegrityError:
                continue
            return prior
    store.put(config, "jobs", job["id"], vault, job)
    return job


def validate(config: Any, db: Any, job: Any, *, sources: bool = True) -> Any:
    if job["maintenance"] != plans.VERSION:
        raise IntegrityError("maintenance_version_changed")
    if not sources:
        from .. import vault_save_policy
        from . import jobs

        root, allowed = packages.context(config, db, job["client_id"], job["vault_id"], write=True)
        epoch = db.execute(
            "SELECT revision FROM graph_permission_epochs WHERE client_id=? AND vault_id=?",
            (job["client_id"], job["vault_id"]),
        ).fetchone()
        fixed = job["fixed"]
        if (
            [root.stat().st_dev, root.stat().st_ino] != fixed["root_key"]
            or allowed.wire() != fixed["scope"]
            or (epoch[0] if epoch else 0) != fixed["permission_epoch"]
            or {
                k: v
                for k, v in vault_save_policy.get(config, db, job["vault_id"]).items()
                if k not in {"mocs", "mocs_truncated"}
            }
            != fixed["save_policy"]
            or jobs.policy(config, job["vault_id"])["revision"] != fixed["policy"]["revision"]
            or plans.analysis_binding(config, db, job["vault_id"]) != fixed["analysis_binding"]
        ):
            raise IntegrityError("maintenance_authorization_or_policy_changed")
        return [], []
    es, ms, fixed = plans.snapshot(config, db, job["client_id"], job["vault_id"], job["mode"])
    if fixed != job["fixed"]:
        raise IntegrityError("maintenance_authorization_or_policy_changed")
    if sources:
        from .maintenance_moc_recovery import planning_mocs
        from .preview_validity import validate_inputs

        validate_inputs(config, db, job, es, ms)
        topics = planning_mocs(config, db, job, ms) + job["new_mocs"]
        context = plans.plan_context(config, job["vault_id"], topics)
        current = {
            e["id"]: plans.source_fingerprint(
                e, topics, fixed, job["mode"], context.decisions.get(e["id"], [])
            )
            for e in es
        }
        handled = manual_resolution.resolved(config, job)
        for item in job["items"]:
            if item["entity"] in handled:
                continue
            if item["entity"] not in current or item["fingerprint"] != current[item["entity"]]:
                raise IntegrityError("maintenance_preview_changed")
    return es, ms


def action(config: Any, db: Any, job: Any, operation: str, expected: str | None) -> Any:
    packages.context(config, db, job["client_id"], job["vault_id"], write=True)
    if operation == "recover_cache":
        from . import cache_recovery

        return cache_recovery.submit(config, db, job)
    if (
        operation == "resume"
        and job.get("index_state") == "failed"
        and job["state"] in {"completed", "partial"}
    ):
        job["index_state"] = "pending"
        job.pop("index_error", None)
        store.put(config, "jobs", job["id"], job["vault_id"], job)
        return job
    if operation == "cancel":
        from .maintenance_undo import cancel

        cancel(config, db, job)
    elif operation == "confirm":
        if (
            job["state"] != "preview"
            or expected != job["plan_sha256"]
            or expected != plans.seal(job)
        ):
            raise InvalidInput("maintenance_preview_confirmation_required")
        from .maintenance_timing import measure

        with measure(job, "confirmation_check"):
            validate(config, db, job)
        from .maintenance_moc_recovery import preflight

        preflight(config, db, job)
        job.update(state="queued", stage="applying", confirmed=True, confirmed_sha256=expected)
        from .maintenance_apply import observe

        job["moc_expected"] = {
            m["id"]: observe(config, db, job, m["id"]) for m in job["mocs"] if not m.get("new")
        }
    elif operation == "recheck":
        if (
            job["confirmed"]
            or job["stage"] != "planning"
            or job["state"] not in {"preview", "blocked"}
        ):
            raise InvalidInput("maintenance_not_resumable")
        validate(config, db, job)
        handled = manual_resolution.resolved(config, job)
        for item in job["items"]:
            if item["entity"] in handled:
                continue
            if item.get("analysis_error") == "organization_model_evidence_invalid":
                item.update(evaluated=False, phase="analysis")
                item.pop("analysis_error", None)
                item.pop("analysis_detail", None)
        job.update(state="queued", error=None)
        job.pop("error_detail", None)
    else:
        from .maintenance_control import action as control

        if not control(config, db, job, operation):
            raise InvalidInput("maintenance_action_invalid")
    store.put(config, "jobs", job["id"], job["vault_id"], job)
    return job


def make_preview(job: Any, entities: Any, mocs: Any) -> Any:
    from . import engine

    job["taxonomy"] = engine.taxonomy(entities, mocs, job["items"])
    job.update(state="preview", completed=0)
    job.pop("expires_at", None)
    job["plan_sha256"] = plans.seal(job)
    return job


def run(config: Any, db: Any, job: Any) -> Any:
    if job["state"] == "cancelling":
        from .maintenance_undo import run as undo

        return undo(config, db, job)
    if job["state"] not in {"queued", "running"}:
        return job
    if job["stage"] == "planning":
        handled = manual_resolution.resolved(config, job)
        if any(not i["evaluated"] and i["entity"] not in handled for i in job["items"]):
            return job
        es, ms = validate(config, db, job)
        make_preview(job, es, ms + job["new_mocs"])
        store.put(config, "jobs", job["id"], job["vault_id"], job)
        return job
    if not job["confirmed"]:
        raise InvalidInput("maintenance_preview_confirmation_required")
    from .maintenance_apply import run as apply

    return apply(config, db, job)


def public(job: Any, *, config: Any = None, db: Any = None) -> Any:
    if config is not None:
        from .manual_resolution import project

        job = project(config, job)
    hidden = {
        "fixed",
        "mocs",
        "policy",
        "moc_expected",
        "permission_epoch",
        "request_key",
        "confirmed_sha256",
        "analysis_history",
    }
    value = {k: v for k, v in job.items() if k not in hidden | {"expires_at"}}
    if config is not None and db is not None and job["state"] == "preview":
        from .preview_validity import status

        value["preview_validity"] = status(config, db, job)
    value["items"] = [
        {k: v for k, v in i.items() if k not in {"source", "decisions", "fingerprint", "intent"}}
        for i in job["items"]
    ]
    value["new_mocs"] = [{k: m[k] for k in ("id", "path", "title")} for m in job["new_mocs"]]
    return value
