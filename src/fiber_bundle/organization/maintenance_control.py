"""Pause/resume and explicit analysis controls, independent of application side effects."""

from typing import Any

from ..config import IntegrityError, InvalidInput
from . import maintenance_plan as plans
from . import manual_resolution


def rebase(config: Any, db: Any, job: Any) -> tuple[Any, Any]:
    entities, mocs, fixed = plans.snapshot(
        config, db, job["client_id"], job["vault_id"], job["mode"]
    )
    if fixed != job["fixed"]:
        raise IntegrityError("maintenance_authorization_or_policy_changed")
    from .maintenance_moc_recovery import planning_mocs

    topics = planning_mocs(config, db, job, mocs) + job["new_mocs"]
    context = plans.plan_context(config, job["vault_id"], topics)
    fresh = {
        e["id"]: plans.item(config, job["vault_id"], e, topics, fixed, job["mode"], context=context)
        for e in entities
    }
    items = []
    handled = manual_resolution.resolved(config, job)
    for previous in job["items"]:
        if previous["entity"] in handled:
            items.append(previous)
            continue
        current = fresh.get(previous["entity"])
        if current is None:
            current = {
                **previous,
                "evaluated": True,
                "selected": [],
                "add": [],
                "remove": [],
                "target": None,
                "analysis_error": "maintenance_note_missing",
            }
        elif current["fingerprint"] == previous["fingerprint"]:
            current = {**previous, "path": current["path"], "source": current["source"]}
            plans.difference(current, job["mode"], fixed["save_policy"]["settings"])
        else:
            job.setdefault("analysis_history", []).append(previous)
        items.append(current)
    job.update(items=items, mocs=topics)
    return entities, mocs


def action(config: Any, db: Any, job: Any, operation: str) -> bool:
    if operation not in {"pause", "resume", "supplement", "retry_unknown", "supplement_missing"}:
        return False
    from . import maintenance

    state = job["state"]
    handled = manual_resolution.resolved(config, job)
    if operation == "supplement_missing":
        if not job.get("cache_recovery") or job["confirmed"] or state != "preview":
            raise InvalidInput("maintenance_not_resumable")
        maintenance.validate(config, db, job)
        pending = [
            i
            for i in job["items"]
            if i.get("analysis_error") == "organization_model_cache_incomplete"
            and i["entity"] not in handled
        ]
        if not pending:
            raise InvalidInput("maintenance_cache_missing_item_required")
        for item in pending:
            item.update(evaluated=False, phase="analysis")
            item.pop("analysis_error", None)
            item.pop("analysis_detail", None)
        job["cache_recovery"]["allow_missing"] = True
        job.update(state="queued", stage="planning", error=None)
        job["generation"] = job.get("generation", 0) + 1
        return True
    if operation == "resume" and state == "rollback_conflict":
        job.update(state="cancelling", error=None)
        return True
    if operation == "pause":
        if state not in {"queued", "running", "preview", "blocked", "partial", "cancelling"}:
            raise InvalidInput("maintenance_not_resumable")
        job.update(state="paused", paused_from=state)
    elif operation == "resume":
        if state not in {"failed", "partial", "blocked", "paused"}:
            raise InvalidInput("maintenance_not_resumable")
        if job.get("stage") == "rollback":
            job.update(state="cancelling", error=None)
            return True
        if job["confirmed"]:
            maintenance.validate(config, db, job, sources=False)
            for item in job["items"]:
                if item["entity"] not in handled and item["state"] in {"failed", "blocked"}:
                    item.update(state="pending", error=None)
            job.update(state="queued", error=None)
        else:
            entities, mocs = rebase(config, db, job)
            job.update(state="queued", error=None)
            if job.get("paused_from") == "preview":
                maintenance.make_preview(job, entities, mocs + job["new_mocs"])
        job.pop("error_detail", None)
    else:
        if job.get("cache_recovery") and operation != "retry_unknown":
            raise InvalidInput("maintenance_cache_only")
        if job["confirmed"] or state not in {"preview", "blocked", "paused"}:
            raise InvalidInput("maintenance_not_resumable")
        maintenance.validate(config, db, job)
        if operation == "retry_unknown":
            from .model_unknown import authorize

            retried = [
                i["entity"]
                for i in job["items"]
                if i.get("analysis_error") == "organization_model_result_unknown"
                and i["entity"] not in handled
            ]
            authorize(config, db, job)
            if job.get("cache_recovery"):
                job["cache_recovery"]["retry_unknown"] = retried
        else:
            for item in job["items"]:
                if (
                    item["entity"] not in handled
                    and not item["selected"]
                    and not item.get("existing_reliable")
                    and not item.get("analysis_error")
                ):
                    item.update(evaluated=False, phase="analysis")
        job.update(state="queued", stage="planning", error=None, model_supplement=True)
    job["generation"] = job.get("generation", 0) + 1
    return True
