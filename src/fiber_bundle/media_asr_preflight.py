"""Live terminology gate surrounding a content-free probe; never an ASR execution permit."""

from typing import Any

from . import media_asr_capabilities, media_glossary, media_jobs
from .config import Config, InvalidInput


def inspect(config: Config, client_id: str, job_id: str) -> dict[str, Any]:
    with media_jobs.connection(config) as db:
        job = media_glossary.access(config, db, client_id, job_id)
        bound = media_jobs.binding(job)
        before = media_glossary.gate(db, job)
        state = job["state"]
    reasons = list(before["blocking_reasons"])
    if before["terminology_gate_passed"]:
        probe = media_asr_capabilities.inspect(bound["image"])
        with media_jobs.connection(config) as db:
            current = media_glossary.access(config, db, client_id, job_id)
            if (
                current["state"] != state
                or media_jobs.binding(current) != bound
                or (media_glossary.gate(db, current) != before)
            ):
                raise InvalidInput("media_glossary_preflight_changed")
        if probe["state"] != "INSPECTED":
            reasons.append("asr_dependency_probe_failed")
        elif any(p["state"] != "FOUND" for p in probe["metadata"]["packages"].values()):
            reasons.append("asr_dependencies_missing_or_rejected")
    else:
        probe = {"state": "NOT_RUN", "model_calls": 0, "media_mounts": 0}
    if bound["subtitle"] is not None:
        reasons.append("existing_subtitle_retained")
    reasons += [
        "asr_model_revision_weights_license_not_bound",
        "asr_resource_acceptance_not_run",
        "asr_execution_not_implemented",
    ]
    return {
        "schema": "fiber-asr-preflight/v1",
        "state": "BLOCKED",
        "blocking_reasons": reasons,
        "terminology": before,
        "dependency_probe": probe,
        "reference_model": "Qwen/Qwen3-ASR-0.6B",
        "model_selection_verified": False,
        "model_revision": None,
        "weights_sha256": None,
        "license_verified": False,
        "asr_ready": False,
        "asr_started": False,
        "model_calls": 0,
        "publication_ready": False,
        "cleanup_allowed": False,
    }
