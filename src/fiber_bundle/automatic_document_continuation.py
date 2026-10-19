"""New immutable ordinary extraction attempts; old inputs and results remain unchanged."""

import json
from typing import Any

from . import automatic_jobs as jobs
from . import source_import_existing, source_imports
from .config import Config, InvalidInput


def enqueue(config: Config, db: Any, job: dict[str, Any], profile: str | None = None) -> str:
    jobs.authorize(config, db, job)
    source = source_imports.get(config, db, job["client_id"], job["source_id"])
    plan = json.loads(source["plan_json"])
    if plan["engine"] != "ordinary" or job["value"].get("publication"):
        raise InvalidInput("automatic_extraction_retry_unavailable")
    original = json.loads(source["request_json"]).get("source_origin") or {}
    imported = source_import_existing.create(
        config,
        db,
        job["client_id"],
        job["vault_id"],
        source["receipt_id"],
        source["asset_id"],
        "ordinary",
        {**original, "retry_of": job["id"]},
        profile=profile or plan["profile"],
    )
    successor = jobs.enqueue(
        config,
        db,
        job["client_id"],
        job["vault_id"],
        "document",
        imported["id"],
        {
            "supersedes_job": job["id"],
            **(
                {"channel_batch": job["value"]["channel_batch"]}
                if job["value"].get("channel_batch")
                else {}
            ),
        },
    )
    if successor is None:
        raise InvalidInput("automatic_extraction_retry_unavailable")
    return successor


def needs_ocr(config: Config, db: Any, job: dict[str, Any], candidate: dict[str, Any]) -> bool:
    source = source_imports.get(config, db, job["client_id"], job["source_id"])
    plan = json.loads(source["plan_json"])
    request = json.loads(source["request_json"])
    if (
        plan["format"] != "pdf"
        or plan["profile"] != "native"
        or request.get("source_origin", {}).get("kind") != "channel_original"
        or not candidate.get("pages_needing_ocr")
    ):
        return False
    from .source_processing_policy import inspect

    if not inspect(config, None)["effective_policy"].get("ocr_runtime"):
        job["value"]["retry_profile"] = "ocr"
        jobs.settle(db, job, "needs_review", reason="ocr_profile_required")
        return True
    enqueue(config, db, job, "ocr")
    jobs.settle(db, job, "needs_review", reason="automatic_ocr_continuation")
    return True
