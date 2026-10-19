"""Structural system assessment of successful article and media candidates."""

import json
import sqlite3
from typing import Any

from . import automatic_jobs as jobs
from . import automatic_policy, automatic_review, processing_assessment
from .config import Config, InvalidInput


def advance(config: Config, db: sqlite3.Connection, job: dict[str, Any]) -> None:
    jobs.authorize(config, db, job)
    identity = job["source_id"]
    if job["kind"] == "article":
        from . import article_publication, article_reviews

        state = db.execute("SELECT state FROM execution_runs WHERE id=?", (identity,)).fetchone()
        if state and state[0] in {"queued", "running"}:
            jobs.settle(db, job, "waiting")
            return
        _, task, verified = article_reviews.capture(config, db, identity)
        source_sha = verified.draft.article.source_sha256
        result_sha = task["manifest_sha256"]
        units = ["body", *["image:" + str(i.index) for i in verified.draft.images]]
        reasons = [] if verified.draft.markdown.strip() else ["article_empty_body"]
        assessment = {
            "source_sha256": source_sha,
            "result_sha256": result_sha,
            "units": [{"unit_id": unit, "decision": "accept"} for unit in units],
        }
        if job["phase"] == "publish":
            automatic_review.credential(
                config, db, job["value"]["review_id"], "article", identity, source_sha, result_sha
            )
            job["value"]["publication"] = article_publication.publish_locked(
                config, db, job["client_id"], identity
            )
            jobs.settle(db, job, "queued", "index")
            return
    elif job["kind"] == "media":
        from . import media_processing, media_publication, media_review

        state = db.execute(
            "SELECT state FROM media_processing WHERE job_id=?", (identity,)
        ).fetchone()
        if state and state[0] in {"queued", "running"}:
            jobs.settle(db, job, "waiting")
            return
        value = media_processing.result_locked(config, db, job["client_id"], identity)
        reasons = [] if value["complete"] else ["media_transcript_or_terminology_incomplete"]
        human = media_review.latest(db, identity)
        if (
            human
            and not media_review.gate(value, json.loads(human["assessment_json"]))[
                "publication_ready"
            ]
        ):
            reasons.append("human_review_blocks_automatic_publication")
        assessment = {
            "source_sha256": value["source_sha256"],
            "result_sha256": value["result_sha256"],
            "units": [
                {"unit_id": u["unit_id"], "decision": "accept"} for u in media_review.units(value)
            ],
            "coverage_ranges": [{"start_ms": 0, "end_ms": value["duration_ms"]}],
            "glossary_revision": value["glossary_revision"],
            "glossary_sha256": value["glossary_sha256"],
        }
        if job["phase"] == "publish":
            job["value"]["publication"] = media_publication.publish_locked(
                config, db, identity, system_review_id=job["value"]["review_id"]
            )
            jobs.settle(db, job, "queued", "index")
            return
    else:
        raise InvalidInput("automatic_candidate_kind_invalid")
    assessment.update(
        task_id=identity,
        decision="uncertain" if reasons else "accept",
        reasons=reasons or ["complete_structurally_verified_extraction"],
        unverified_dimensions=[
            "semantic_correctness",
            "human_audio_visual_review",
            "independent_review",
        ],
    )
    if reasons:
        for unit in assessment["units"]:
            unit["decision"] = "uncertain"
    settings = job["value"]["binding"]["settings"]
    if not processing_assessment.checks(settings):
        if "human_review_blocks_automatic_publication" in reasons:
            raise InvalidInput("human_review_blocks_automatic_publication")
        assessment = processing_assessment.unchecked(
            assessment["source_sha256"], assessment["result_sha256"], task=identity, gaps=reasons
        )
    job["value"]["review_id"] = automatic_review.issue(config, db, job, assessment)
    jobs.settle(
        db,
        job,
        "needs_review"
        if not processing_assessment.saveable(assessment)
        else "queued"
        if automatic_policy.direct(settings)
        else "reviewed",
        "publish",
        reasons[0] if reasons else None,
    )
