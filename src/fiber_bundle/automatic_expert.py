"""Complete-package structural admission; independent semantic review remains deferred."""

import json
import sqlite3
from typing import Any

from . import automatic_jobs as jobs
from . import (
    automatic_policy,
    automatic_review,
    expert_dispatch,
    expert_evidence,
    expert_publication,
    expert_tasks,
    processing_assessment,
)
from .config import Config, InvalidInput
from .inputs import canonical
from .objects import digest


def advance(config: Config, db: sqlite3.Connection, job: dict[str, Any]) -> None:
    jobs.authorize(config, db, job)
    identity = job["source_id"]
    from .expert_authoring import projection as authoring_projection

    authored = authoring_projection(db, identity)
    if job["phase"] == "extract":
        expert_tasks.prepare(config, db, job["client_id"], identity)
        jobs.settle(db, job, "waiting", "review")
        if not authored or authored["state"] not in {"launching", "running"}:
            expert_dispatch.launch(config, db, job["client_id"], identity)
        return
    task = expert_tasks.get(db, identity)
    if task is None:
        raise InvalidInput("automatic_expert_not_admitted")
    if task["state"] != "completed" and authored and authored["state"] != "complete":
        jobs.settle(
            db,
            job,
            "waiting"
            if authored["state"] in {"queued", "launching", "running"}
            else "needs_review",
            reason=authored["code"],
        )
        return
    if task["state"] in {"prepared", "running"}:
        jobs.settle(
            db,
            job,
            "reconcile" if task["launch_state"] != "launched" else "waiting",
            reason="automatic_expert_launch_not_proven"
            if task["launch_state"] != "launched"
            else None,
        )
        return
    if task["state"] != "completed":
        outcome = json.loads(task["result_json"]) if task["result_json"] else {}
        jobs.settle(
            db,
            job,
            "needs_review",
            reason=outcome.get("code") or "automatic_expert_" + task["state"],
        )
        return
    _, packet, files = expert_evidence.load(config, db, job["client_id"], identity)
    gaps = authored["known_gaps"] if authored else []
    if job["phase"] == "review":
        assessment = {
            "task_id": identity,
            "decision": "uncertain" if gaps else "accept",
            "reasons": gaps or ["complete_canonical_draft_package_verified"],
            "units": [{"unit_id": path, "decision": "accept"} for path in sorted(files)],
            "actor": "system_rules",
            "source_sha256": packet["source_sha256"],
            "result_sha256": digest(canonical(packet).encode()),
            "artifact_stage": "draft",
            "semantic_review": "deferred",
            "knowledge_verified": False,
        }
        settings = job["value"]["binding"]["settings"]
        if not processing_assessment.checks(settings):
            assessment = processing_assessment.unchecked(
                packet["source_sha256"],
                digest(canonical(packet).encode()),
                task=identity,
                gaps=gaps,
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
        )
    elif job["phase"] == "publish":
        job["value"]["publication"] = expert_publication.publish(
            config,
            db,
            job["client_id"],
            identity,
            digest(canonical(packet).encode()),
            False,
            False,
            system_review_id=job["value"]["review_id"],
        )
        jobs.settle(db, job, "queued", "index")
    else:
        raise InvalidInput("automatic_expert_phase_invalid")
