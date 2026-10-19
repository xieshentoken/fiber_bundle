"""Structural rule assessments. No human assertions, model inference or semantic certification."""

import json
import sqlite3
import time
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from . import automatic_jobs, document_review, processing_assessment
from .config import Config, InvalidInput
from .inputs import canonical
from .objects import digest


def document(
    candidate: dict[str, Any], metadata: dict[str, Any], human: sqlite3.Row | None
) -> dict[str, Any]:
    units = document_review.units(candidate)
    reasons = []
    if candidate.get("complete") is False or not units:
        reasons.append("source_incomplete")
    if metadata.get("missing_references"):
        reasons.append("missing_attachment_mapping")
    source = candidate.get("pages", candidate.get("segments", []))
    # Blank Office paragraphs/cells are valid source units; the complete capture
    # must still contain actual text or a stored formula, and hard parser flags remain below.
    if not any(u.get("text", "").strip() or u.get("locator", {}).get("formula") for u in source):
        reasons.append("empty_source_unit")
    # Literal/native numbers are preserved extraction, never validated technical claims.
    permitted = {
        "numeric_or_unit_review",
        "source_fields_unknown",
        "table_review",
        "formula_review",
        "visual_review",
        "ocr_review",
        "math_font_present",
        "equation_label_present",
        "figure_reference_present",
        "table_reference_present",
        "raster_image_present",
    }
    if any(u["ocr_reasons"] or set(u["review_reasons"]) - permitted for u in units):
        reasons.append("source_requires_manual_review")
    if human is not None:
        assessment = document_review.checked(candidate, json.loads(human["assessment_json"]))
        if any(j.verdict != "accept" for j in assessment.judgments):
            reasons.append("human_review_blocks_automatic_publication")
    return {
        "decision": "uncertain" if reasons else "accept",
        "reasons": reasons or ["complete_structurally_verified_extraction"],
        "units": [
            {"unit_id": u["unit_id"], "decision": "uncertain" if reasons else "accept"}
            for u in units
        ],
        "unverified_dimensions": [
            "semantic_correctness",
            "independent_gold_review",
            "human_visual_review",
            "formula_evaluation",
            "source_author_and_date",
        ],
        "source_sha256": candidate["source_sha256"],
        "result_sha256": candidate["result_sha256"],
    }


def issue(
    config: Config,
    db: sqlite3.Connection,
    job: dict[str, Any],
    assessment: dict[str, Any],
    *,
    model: bool = False,
) -> str | None:
    automatic_jobs.authorize(config, db, job)
    if (
        job["value"]["binding"]["settings"].get("policy_version", 1) == 1
        and job["value"]["binding"]["settings"]["review_method"] == "model"
        and assessment["decision"] == "accept"
        and not model
    ):
        job["value"]["pending_model"] = assessment
        return None
    actor = "system_model" if model else "system_rules"
    assessment = processing_assessment.status(assessment)
    identity = str(uuid5(NAMESPACE_URL, "fiber-system-review:" + job["id"]))
    value = {
        **assessment,
        "schema": "system-save-credential/v1"
        if assessment["decision"] == "save_unchecked"
        else "system-review-credential/v1",
        "task_id": assessment.get("task_id", job["source_id"]),
        "kind": job["kind"],
        "vault_id": job["vault_id"],
        "client_id": job["client_id"],
        "binding": job["value"]["binding"],
        "policy_revision": job["policy_revision"],
        "actor": actor,
        "model_run_refs": job["value"].get("model_run_refs", []) if model else [],
        "knowledge_verified": False,
    }
    old = db.execute("SELECT value FROM automatic_reviews WHERE id=?", (identity,)).fetchone()
    if old is not None and old[0] != canonical(value):
        raise InvalidInput("automatic_review_inputs_changed")
    db.execute(
        "INSERT OR IGNORE INTO automatic_reviews VALUES(?,?,?,?,?)",
        (identity, job["id"], actor, canonical(value), time.time()),
    )
    return identity


def credential(
    config: Config,
    db: sqlite3.Connection,
    identity: str,
    kind: str,
    task: str,
    source_sha: str,
    result_sha: str,
) -> dict[str, Any]:
    row = db.execute("SELECT * FROM automatic_reviews WHERE id=?", (identity,)).fetchone()
    if row is None:
        raise InvalidInput("automatic_review_unavailable")
    job = automatic_jobs.get(db, row["job_id"])
    automatic_jobs.authorize(config, db, job)
    value = json.loads(row["value"])
    settings = job["value"]["binding"]["settings"]
    expected_decision = "save_unchecked" if not processing_assessment.checks(settings) else "accept"
    if (
        value["kind"],
        value["task_id"],
        value["source_sha256"],
        value["result_sha256"],
        value["decision"],
    ) != (kind, task, source_sha, result_sha, expected_decision):
        raise InvalidInput("automatic_review_binding_changed")
    # Renderers receive an honest typed assessment; it is never written to human review tables.
    return {
        "id": identity,
        "actor": row["actor"],
        "assessment_json": canonical(value),
        "system_review": True,
        "completeness_status": value.get("completeness_status", "unknown"),
        "save_basis": value.get("save_basis", "checked_system_policy"),
        "credential_sha256": digest(canonical(value).encode()),
    }
