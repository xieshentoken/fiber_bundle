"""Create a bounded follow-up preview; legacy calls are only read, never resubmitted."""

from typing import Any

from ..config import IntegrityError, InvalidInput
from . import (
    maintenance,
    maintenance_plan,
    manual_resolution,
    model_evidence,
    model_review,
    semantic,
    store,
    text_features,
)
from .model_unknown import UnknownCall


def profiles(mocs: Any) -> Any:
    return sorted(
        (p["id"], p["title"], p["text"]) for p in (text_features.moc_profile(m) for m in mocs)
    )


def binding(parent: Any, fixed: Any, mocs: Any) -> None:
    if any(
        parent["fixed"][k] != fixed[k]
        for k in ("root_key", "scope", "permission_epoch", "analysis_binding", "policy")
    ) or profiles(parent["mocs"]) != profiles(mocs):
        raise IntegrityError("maintenance_cache_binding_changed")


def submit(config: Any, db: Any, parent: Any) -> Any:
    if parent.get("cache_recovery"):
        original = store.get(config, "jobs", parent["cache_recovery"]["parent"])
        if not original or original.get("cache_recovery"):
            raise IntegrityError("maintenance_cache_binding_changed")
        parent = original
    if parent["state"] != "completed":
        raise InvalidInput("maintenance_cache_completed_task_required")
    # Active follow-ups are idempotent. Later rounds exclude applied results.
    applied: set[str] = set()
    applied.update(manual_resolution.resolved(config, parent))
    for prior in store.rows(config, "jobs", parent["vault_id"]):
        recovery = prior.get("cache_recovery", {})
        if (
            recovery.get("parent") == parent["id"]
            and recovery.get("version") == model_evidence.VERSION
            and prior["state"] != "cancelled"
        ):
            applied.update(manual_resolution.resolved(config, prior))
            if prior["state"] != "completed":
                return prior
            applied.update(
                i["entity"] for i in prior["items"] if i.get("selected") and i["state"] == "done"
            )
    entities, mocs, fixed = maintenance_plan.snapshot(
        config, db, parent["client_id"], parent["vault_id"], parent["mode"]
    )
    binding(parent, fixed, mocs)
    current = {e["id"]: e for e in entities}
    context = maintenance_plan.plan_context(config, parent["vault_id"], mocs)
    wanted = []
    for old in parent["items"]:
        if (
            old["entity"] in applied
            or old.get("selected")
            or not (old.get("analysis_error") or old["status"] == "review")
        ):
            continue
        entity = current.get(old["entity"])
        if entity is None or entity["sha256"] != old["sha256"]:
            raise IntegrityError("maintenance_note_changed")
        fresh = maintenance_plan.item(
            config, parent["vault_id"], entity, mocs, fixed, parent["mode"], context=context
        )
        if fresh["existing_reliable"]:
            continue
        if fresh["decisions"] != old["decisions"]:
            raise IntegrityError("maintenance_manual_decision_changed")
        wanted.append(old["entity"])
    if not wanted:
        raise InvalidInput("maintenance_cache_unresolved_required")
    return maintenance.submit(
        config,
        db,
        parent["client_id"],
        parent["vault_id"],
        parent["mode"],
        recovery={"parent": parent["id"], "version": model_evidence.VERSION, "entities": wanted},
    )


def resolve(config: Any, job: Any, entity: Any, mocs: Any, item: Any, check: Any) -> Any:
    parent = store.get(config, "jobs", job["cache_recovery"]["parent"])
    if not parent or parent["state"] != "completed":
        raise IntegrityError("maintenance_cache_binding_changed")
    binding(parent, job["fixed"], mocs)
    old = next(i for i in parent["items"] if i["entity"] == item["entity"])
    if old["sha256"] != entity["sha256"] or old["decisions"] != item["decisions"]:
        raise IntegrityError("maintenance_note_changed")
    retry_unknown = item["entity"] in job["cache_recovery"].get("retry_unknown", [])
    if old.get("analysis_error") == "organization_model_result_unknown" and not retry_unknown:
        # Unknown transport is never a reason to reconstruct or send another call.
        raise UnknownCall(old["analysis_detail"])
    recall = semantic.recall(config, parent, entity, parent["mocs"], check, cached_only=True)
    return model_review.resolve(
        config,
        job,
        entity,
        parent["mocs"],
        {**old, "semantic": recall},
        check,
        cached_only=not (job["cache_recovery"].get("allow_missing", False) or retry_unknown),
    )
