"""Automatic structural admission of new, fully captured channel link content."""

import json
import sqlite3
from typing import Any

from . import automatic_jobs as jobs
from . import automatic_policy, automatic_review, processing_assessment
from .config import Config, InvalidInput
from .inputs import canonical
from .objects import digest


def source_digest(link: dict[str, Any]) -> str:
    return digest(canonical({"url": link["original_url"], "receipt": link["receipt"]}).encode())


def advance(config: Config, db: sqlite3.Connection, job: dict[str, Any]) -> None:
    from .channels import intake_reviews, store
    from .channels.intake_processing import guard_locked

    jobs.authorize(config, db, job)
    context = job["value"]["intake"]
    with store.readonly(config) as side:
        if side is None:
            raise InvalidInput("automatic_intake_scope_changed")
        row = side.execute(
            "SELECT * FROM channel_intake_records WHERE channel_id=? AND batch_id=?",
            (context["channel"], context["batch"]),
        ).fetchone()
        if row is None or row["revision"] != context["revision"]:
            raise InvalidInput("automatic_intake_scope_changed")
        saved = json.loads(row["value"])
        guard_locked(
            config, context["channel"], context["revision"], manual=bool(saved.get("manual"))
        )
        if saved["target_vault_id"] != job["vault_id"]:
            raise InvalidInput("automatic_intake_target_changed")
        pending = saved["links"][context["index"]]
        if context.get("attempt_id") and context["attempt_id"] != pending.get("attempt_id"):
            raise InvalidInput("automatic_intake_scope_changed")
        if pending.get("state") in {None, "pending", "extracting"}:
            jobs.settle(db, job, "waiting")
            return
        if not pending.get("receipt") or pending.get("state") not in {
            "review_required",
            "published",
        }:
            jobs.settle(
                db,
                job,
                "needs_review",
                reason=pending.get("reason") or "automatic_intake_incomplete",
            )
            return
        _, _, value, files = intake_reviews.load(
            config, db, side, context["channel"], context["batch"], context["index"]
        )
    link = value["links"][context["index"]]
    sha = digest(canonical(link["files"]).encode())
    original = source_digest(link)
    if not files.get("note.md", b"").strip():
        raise InvalidInput("automatic_intake_empty_body")
    reasons = list(link.get("processing_gaps", []))
    if link.get("reason") or link.get("state") not in {"review_required", "published"}:
        reasons.append("automatic_intake_incomplete")
    if not files.get("note.md", b"").strip():
        reasons.append("automatic_intake_empty_body")
    if job["phase"] == "review":
        assessment = {
            "task_id": job["source_id"],
            "source_sha256": original,
            "result_sha256": sha,
            "decision": "uncertain" if reasons else "accept",
            "reasons": reasons or ["complete_structurally_verified_channel_capture"],
            "units": [
                {"unit_id": name, "decision": "uncertain" if reasons else "accept"}
                for name in sorted(files)
            ],
            "analysis_cautions": link.get("processing_cautions", []),
            "unverified_dimensions": [
                "human_source_review",
                "semantic_correctness",
                "independent_review",
            ],
        }
        settings = job["value"]["binding"]["settings"]
        if not processing_assessment.checks(settings):
            assessment = processing_assessment.unchecked(
                original, sha, task=job["source_id"], gaps=reasons
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
    elif job["phase"] == "publish":
        automatic_review.credential(
            config, db, job["value"]["review_id"], "intake", job["source_id"], original, sha
        )
        job["value"]["publication"] = intake_reviews.publish(
            config,
            context["channel"],
            context["batch"],
            context["index"],
            "",
            leased=True,
            system_review_id=job["value"]["review_id"],
            automatic_task=job["source_id"],
        )
        jobs.settle(db, job, "queued", "index")
    else:
        raise InvalidInput("automatic_intake_phase_invalid")


def resume_capture(
    config: Config,
    db: sqlite3.Connection,
    channel: str,
    batch: str,
    index: int,
    *,
    attempt_id: str | None = None,
) -> None:
    """An explicit retry may replace an unreviewed cancelled job, preserving its history."""
    from uuid import NAMESPACE_URL, uuid5

    from .intake_abort_store import guard

    guard(config, {"channel": channel, "batch": batch, "index": index})
    rows = db.execute(
        "SELECT j.id FROM automatic_jobs j WHERE kind='intake' "
        "AND json_extract(j.value,'$.intake.channel')=? "
        "AND json_extract(j.value,'$.intake.batch')=? "
        "AND json_extract(j.value,'$.intake.index')=? "
        "AND NOT EXISTS(SELECT 1 FROM automatic_jobs s WHERE s.vault_id=j.vault_id "
        "AND s.client_id=j.client_id AND s.kind=j.kind "
        "AND json_extract(s.value,'$.supersedes_job')=j.id)",
        (channel, batch, index),
    ).fetchall()
    if len(rows) > 1:
        raise InvalidInput("automatic_intake_scope_changed")
    for row in rows:
        job = jobs.get(db, row[0])
        if job["state"] not in {"needs_review", "cancelled"}:
            continue
        if job["phase"] != "review" or job["value"].get("review_id"):
            if job["state"] == "cancelled":
                raise InvalidInput("automatic_reconciliation_unavailable")
            continue
        if job["state"] == "cancelled" or job["value"]["intake"].get("attempt_id"):
            if not attempt_id or job["value"].get("publication"):
                raise InvalidInput("automatic_reconciliation_unavailable")
            binding = automatic_policy.binding(
                config, db, job["vault_id"], job["client_id"], "intake"
            )
            old_binding = {
                k: v for k, v in job["value"]["binding"].items() if k != "processing_model_sha256"
            }
            if binding is None or old_binding != binding:
                raise InvalidInput("automatic_authorization_changed")
            guard(config, {**job["value"]["intake"], "job_id": job["id"]})
            source = str(
                uuid5(NAMESPACE_URL, f"fiber-intake-retry:{channel}:{batch}:{index}:{attempt_id}")
            )
            jobs.enqueue(
                config,
                db,
                job["client_id"],
                job["vault_id"],
                "intake",
                source,
                {
                    "intake": {**job["value"]["intake"], "attempt_id": attempt_id},
                    "supersedes_job": job["id"],
                },
            )
        else:
            jobs.authorize(config, db, job)
            jobs.settle(db, job, "waiting", reason=None)


def resume_analysis(
    config: Config,
    db: sqlite3.Connection,
    channel: str,
    batch: str,
    index: int,
    previous_cautions: list[str],
) -> None:
    """Continue a corrected derived result; preserve the original immutable review/job."""
    from uuid import NAMESPACE_URL, uuid5

    if not previous_cautions:
        return
    for row in db.execute(
        "SELECT j.id,r.value FROM automatic_jobs j JOIN automatic_reviews r ON r.job_id=j.id "
        "WHERE j.kind='intake' AND j.state='needs_review' AND j.phase='publish' "
        "AND r.actor='system_rules' AND json_extract(j.value,'$.intake.channel')=? "
        "AND json_extract(j.value,'$.intake.batch')=? AND json_extract(j.value,'$.intake.index')=?",
        (channel, batch, index),
    ).fetchall():
        assessment = json.loads(row["value"])
        if (
            assessment["decision"] != "uncertain"
            or not assessment.get("reasons")
            or not set(assessment["reasons"]).issubset(previous_cautions)
        ):
            continue
        old = jobs.get(db, row["id"])
        jobs.authorize(config, db, old)
        source = str(uuid5(NAMESPACE_URL, "fiber-analysis-cautions-v2:" + old["source_id"]))
        jobs.enqueue(
            config,
            db,
            old["client_id"],
            old["vault_id"],
            "intake",
            source,
            {"intake": old["value"]["intake"], "supersedes_job": old["id"]},
        )
