"""Analyze outside the writer lock; preview never writes MOC files or moves notes."""

from typing import Any

from .. import database
from ..config import IntegrityError, InvalidInput
from . import (
    maintenance,
    manual_resolution,
    model_evidence,
    model_review,
    semantic,
    store,
    text_features,
)
from . import maintenance_plan as plans
from .maintenance_guard import AnalysisGuard
from .model_unknown import UnknownCall


def prepare(config: Any, identity: str) -> None:
    with database.locked(config), database.connect(config.state_root) as db:
        job = store.get(config, "jobs", identity)
        if not job or job["state"] not in {"queued", "running"} or job["stage"] != "planning":
            return
    handled = manual_resolution.resolved(config, job)
    if (
        job["mode"] == "quick"
        and not job.get("cache_recovery")
        and not job.get("model_supplement", False)
        and any(not i["evaluated"] and i["entity"] not in handled for i in job["items"])
    ):
        from . import quick_analysis

        quick_analysis.prepare(config, job)
        return
    # No writer lease is held during the complete vault read and plan comparison.
    with database.connect(config.state_root, readonly=True) as db:
        es, ms = maintenance.validate(config, db, job)
    item = next(
        (i for i in job["items"] if not i["evaluated"] and i["entity"] not in handled), None
    )
    entity = next((e for e in es if item and e["id"] == item["entity"]), None)
    mocs = ms + job["new_mocs"]
    check = AnalysisGuard(config, job, entity, mocs)

    result: dict[str, Any] = {}
    try:
        check()
        if item is None:
            preview = maintenance.make_preview(dict(job), es, mocs)
            check()
            with database.locked(config), database.connect(config.state_root) as db:
                latest = check.authorized(db)
                if latest["items"] != job["items"]:
                    return
                store.put(config, "jobs", identity, job["vault_id"], preview)
            return
        assert entity is not None
        if (
            sum(
                len(p["text"])
                for p in text_features.passages(entity["text"])
                if p["kind"] == "body"
            )
            >= 20
        ):
            try:
                if job.get("cache_recovery"):
                    from . import cache_recovery

                    result.update(cache_recovery.resolve(config, job, entity, mocs, item, check))
                else:
                    try:
                        result["semantic"] = semantic.recall(config, job, entity, mocs, check)
                    except (OSError, InvalidInput) as e:
                        result["semantic"] = {"state": "unavailable", "error": str(e)[:160]}
                    result.update(
                        model_review.resolve(config, job, entity, mocs, {**item, **result}, check)
                    )
            except InvalidInput as e:
                if str(e) not in {
                    "processing_model_unconfigured",
                    "model_unconfigured",
                    "processing_cloud_content_not_allowed",
                }:
                    raise
                result["analysis_error"] = str(e)
        else:
            result.update(review_reason="body_text_required", model_called=False)
        check()
        with database.locked(config), database.connect(config.state_root) as db:
            latest = check.authorized(db)
            current = next(i for i in latest["items"] if i["entity"] == item["entity"])
            if current["evaluated"] or item["entity"] in manual_resolution.resolved(config, latest):
                return
            current.update(result, evaluated=True, phase="ready")
            plans.difference(current, job["mode"], job["fixed"]["save_policy"]["settings"])
            latest["model_called"] |= current["model_called"]
            from .quick_analysis import remember

            current["analysis_origin"] = "model_cache" if not current["model_called"] else "model"
            remember(config, latest, current)
            store.put(config, "jobs", identity, job["vault_id"], latest)
    except Exception as e:
        with database.locked(config):
            latest = store.get(config, "jobs", identity)
            if latest["state"] not in {"queued", "running"} or latest.get(
                "generation", 0
            ) != job.get("generation", 0):
                return
            if item and item["entity"] in manual_resolution.resolved(config, latest):
                return
            if (
                isinstance(e, (model_evidence.EvidenceInvalid, UnknownCall))
                or str(e) == "organization_model_cache_incomplete"
            ):
                assert item is not None
                current = next(i for i in latest["items"] if i["entity"] == item["entity"])
                current.update(
                    evaluated=True,
                    phase="ready",
                    analysis_error=str(e),
                    analysis_detail=e.detail
                    if isinstance(e, (UnknownCall, model_review.CacheIncomplete))
                    else getattr(e, "reason", "cache_incomplete"),
                )
                plans.difference(current, job["mode"], job["fixed"]["save_policy"]["settings"])
                latest.update(error=None)
                latest.pop("error_detail", None)
                store.put(config, "jobs", identity, job["vault_id"], latest)
                return
            latest.update(
                state="blocked",
                error=str(e)[:180]
                if isinstance(e, (IntegrityError, InvalidInput))
                else type(e).__name__,
            )
            latest.pop("error_detail", None)
            store.put(config, "jobs", identity, job["vault_id"], latest)
