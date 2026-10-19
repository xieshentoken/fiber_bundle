"""Local, incremental maintenance classification; never invokes embedding or model services."""

from typing import Any

from .. import database
from ..config import InvalidInput
from ..inputs import canonical
from ..objects import digest
from . import maintenance, manual_resolution, store
from . import maintenance_plan as plans
from .maintenance_guard import AnalysisGuard

RESULT_FIELDS = {
    "selected",
    "candidates",
    "model_used",
    "model_configuration_sha256",
    "model_evidence",
    "body_windows",
    "model_response_reused",
    "review_reason",
}


def key(job: Any, item: Any) -> str:
    # Classification depends on source, authorization and topic definitions, not
    # the selected archive mode or destination. Application is always replanned.
    fixed = job["fixed"]
    return digest(
        canonical(
            [
                "classification/v1",
                job["client_id"],
                job["vault_id"],
                item["entity"],
                item["sha256"],
                {
                    k: fixed[k]
                    for k in ("root_key", "scope", "policy", "permission_epoch", "analysis_binding")
                },
                [[m["id"], m["sha256"]] for m in job["mocs"]],
                item["decisions"],
            ]
        ).encode()
    )


def remember(config: Any, job: Any, item: Any) -> None:
    if item.get("analysis_error"):
        return
    store.put(
        config,
        "analysis_cache",
        key(job, item),
        job["vault_id"],
        {
            "version": 1,
            "result": {k: item[k] for k in RESULT_FIELDS if k in item},
            "origin": item.get("analysis_origin", "model" if item.get("model_used") else "local"),
        },
    )


def legacy_results(config: Any, job: Any, entities: Any, keys: Any) -> dict[str, Any]:
    """Revalidate complete legacy answers locally; incomplete/unknown windows never send."""
    from . import model_review

    result = {}
    for previous in store.rows(config, "jobs", job["vault_id"]):
        if not previous.get("maintenance") or previous["id"] == job["id"]:
            continue
        for item in previous["items"]:
            identity = item["entity"]
            if (
                identity not in keys
                or identity in result
                or not item.get("evaluated")
                or not item.get("model_used")
                or item.get("analysis_error")
                or key(previous, item) != keys[identity]
            ):
                continue
            try:
                value = model_review.resolve(
                    config,
                    previous,
                    entities[identity],
                    previous["mocs"],
                    item,
                    lambda: None,
                    cached_only=True,
                )
            except InvalidInput:
                continue
            result[identity] = value
    return result


def prepare(config: Any, job: Any) -> None:
    """One allowed-scope snapshot for the entire local round, rather than per note."""
    with database.connect(config.state_root, readonly=True) as db:
        entities, mocs = maintenance.validate(config, db, job)
    guard = AnalysisGuard(config, job, None, mocs + job["new_mocs"])
    guard()
    handled = manual_resolution.resolved(config, job)
    keys = {
        i["entity"]: key(job, i)
        for i in job["items"]
        if not i["evaluated"] and i["entity"] not in handled
    }
    cached = store.get_many(config, "analysis_cache", list(keys.values()))
    legacy = legacy_results(
        config,
        job,
        {e["id"]: e for e in entities},
        {i: k for i, k in keys.items() if k not in cached},
    )
    context = plans.plan_context(config, job["vault_id"], job["mocs"])
    fresh = {
        e["id"]: plans.item(
            config, job["vault_id"], e, job["mocs"], job["fixed"], job["mode"], context=context
        )
        for e in entities
    }
    with database.locked(config), database.connect(config.state_root) as db:
        latest = guard.authorized(db)
        # A pause/resume or model-supplement request must invalidate this round.
        if latest.get("generation", 0) != job.get("generation", 0):
            return
        values = {}
        handled = manual_resolution.resolved(config, latest)
        for item in latest["items"]:
            if item["evaluated"] or item["entity"] in handled:
                continue
            hit = cached.get(keys[item["entity"]])
            inherited = legacy.get(item["entity"])
            result = hit["result"] if hit else inherited or fresh[item["entity"]]
            item.update({k: result[k] for k in RESULT_FIELDS if k in result})
            item.update(
                evaluated=True,
                phase="ready",
                analysis_origin="cache" if hit else "model_cache" if inherited else "local",
                model_called=False,
            )
            item.pop("analysis_error", None)
            item.pop("analysis_detail", None)
            plans.difference(item, job["mode"], job["fixed"]["save_policy"]["settings"])
            values[keys[item["entity"]]] = hit or {
                "version": 1,
                "origin": "local",
                "result": {k: item[k] for k in RESULT_FIELDS if k in item},
            }
        store.put_many(config, "analysis_cache", job["vault_id"], values)
        latest.update(error=None)
        latest.pop("error_detail", None)
        store.put(config, "jobs", latest["id"], job["vault_id"], latest)
