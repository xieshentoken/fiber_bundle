"""Explicit proof-based continuation, never a new charge or a replacement authorization."""

import json
import sqlite3
from typing import Any

from . import automatic_jobs as jobs
from . import publication_file_io, publication_locations, vault_directories
from .config import Config, IntegrityError, InvalidInput
from .objects import digest


def resume(
    config: Config,
    db: sqlite3.Connection,
    vault: str,
    identity: str,
    *,
    approve_new_model_attempt: bool = False,
) -> dict[str, Any]:
    job = jobs.get(db, identity)
    if job["vault_id"] != vault or job["state"] not in {"reconcile", "needs_review"}:
        raise InvalidInput("automatic_reconciliation_unavailable")
    jobs.authorize(config, db, job)
    if job["kind"] == "intake_summary" and job["phase"] == "extract":
        from .channels.intake_pi import prepare_retry

        part = {
            "runs": job["value"].setdefault("runs", {}),
            "previous_runs": job["value"].setdefault("previous_runs", []),
            "rejected_runs": job["value"].get("rejected_runs", []),
        }
        prepare_retry(db, {"pi_processing": part}, approve_new_attempt=approve_new_model_attempt)
        jobs.settle(db, job, "queued", "extract")
        return {
            "state": "queued",
            "phase": "extract",
            "original_attempt_preserved": True,
            "new_model_attempt": approve_new_model_attempt,
        }
    if job["kind"] == "document" and not job["value"].get("chat_archive"):
        source = db.execute(
            "SELECT * FROM source_imports WHERE id=?", (job["source_id"],)
        ).fetchone()
        run = db.execute(
            "SELECT state FROM execution_runs WHERE id=?", (job["value"].get("run_id"),)
        ).fetchone()
        if source and (
            job["value"].get("retry_profile")
            or (run and run[0] in {"failed", "cancelled", "interrupted"})
            or (not source["run_id"] and job["phase"] == "extract")
        ):
            from .automatic_document_continuation import enqueue

            successor = enqueue(config, db, job, job["value"].get("retry_profile"))
            return {
                "state": "queued",
                "successor_job_id": successor,
                "new_model_attempt": False,
                "original_attempt_preserved": True,
            }
    phase = job["phase"]
    if phase == "model_review":
        raise InvalidInput("automatic_model_unknown_requires_execution_accounting_and_human_review")
    if phase == "extract":
        # No execution request exists: explicitly continue only the same frozen input.
        table, column = (
            ("source_expert_tasks", "id") if job["kind"] == "expert" else ("source_imports", "id")
        )
        row = db.execute(f"SELECT * FROM {table} WHERE {column}=?", (job["source_id"],)).fetchone()
        if (job["kind"] == "expert" and row is not None) or (
            job["kind"] == "document" and row and row["run_id"]
        ):
            raise InvalidInput("automatic_launch_unknown_use_task_reconciliation")
    elif phase == "publish":
        if not job["value"].get("review_id"):
            raise InvalidInput("automatic_valid_review_required")
        review = db.execute(
            "SELECT value FROM automatic_reviews WHERE id=?", (job["value"]["review_id"],)
        ).fetchone()
        if not review or json.loads(review[0])["decision"] != "accept":
            raise InvalidInput("automatic_valid_review_required")
        pubid = job["source_id"]
        if job["kind"] == "document" and not job["value"].get("chat_archive"):
            pubid = job["value"]["run_id"]
        elif job["kind"] == "article":
            pub = db.execute(
                "SELECT publication_id FROM article_publication_runs WHERE run_id=?", (pubid,)
            ).fetchone()
            pubid = pub[0] if pub else pubid
        elif job["kind"] == "intake":
            from uuid import NAMESPACE_URL, uuid5

            context = job["value"]["intake"]
            pubid = str(
                uuid5(
                    NAMESPACE_URL,
                    "fiber-intake:"
                    + context["channel"]
                    + ":"
                    + context["batch"]
                    + ":"
                    + str(context["index"]),
                )
            )
        location = publication_locations.get(
            db, "intake" if job["value"].get("chat_archive") else job["kind"], pubid
        )
        if location:
            root, _, _ = vault_directories.vault_root(config, db, vault)
            for path in location["plan"]["outputs"]:
                claim = location["plan"]["claims"].get(path)
                if claim:
                    raw, key = publication_file_io.read(root, path)
                    if (
                        key[:2] != claim
                        or digest(raw)
                        != location["plan"].get(
                            "write_hashes", location["plan"]["published_hashes"]
                        )[path]
                    ):
                        raise IntegrityError(
                            "automatic_publication_copy_requires_manual_reconciliation"
                        )
                elif (root / path).exists():
                    raise IntegrityError("automatic_publication_unclaimed_file_exists")
    elif phase not in {"review", "index"}:
        raise InvalidInput("automatic_stage_not_recoverable")
    job["value"].setdefault("failure_history", []).append(
        {"phase": phase, "reason": job.get("reason"), "failure": job["value"].pop("failure", None)}
    )
    jobs.settle(db, job, "queued", phase)
    return {
        "state": "queued",
        "phase": phase,
        "same_frozen_authorization": True,
        "new_model_attempt": False,
    }
