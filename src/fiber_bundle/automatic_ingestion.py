"""Finite durable automation ticks; browser reads never launch or replay work."""

import json
import logging
import sqlite3
import threading
from typing import Any

from . import auth, database, source_imports
from . import automatic_jobs as jobs
from .config import Config, InvalidInput, StorageError


def document_tick(config: Config, db: sqlite3.Connection, job: dict[str, Any]) -> None:
    from . import (
        automatic_policy,
        automatic_review,
        document_evidence,
        document_publication,
        document_review,
        processing_assessment,
        source_import_execution,
    )

    jobs.authorize(config, db, job)
    source = source_imports.get(config, db, job["client_id"], job["source_id"])
    if job["phase"] == "extract":
        run = source_import_execution.prepare(config, db, job["client_id"], source["id"])
        job["value"]["run_id"] = run
        jobs.settle(db, job, "waiting", "review")  # Persist before the one launch attempt.
        source_import_execution.launch(config, db, job["client_id"], source["id"])
        return
    run = job["value"]["run_id"]
    state = db.execute("SELECT state FROM execution_runs WHERE id=?", (run,)).fetchone()[0]
    if state in {"queued", "running"}:
        launch = db.execute(
            "SELECT launch_state FROM source_task_bindings WHERE run_id=?", (run,)
        ).fetchone()[0]
        jobs.settle(
            db,
            job,
            "waiting" if launch == "launched" else "reconcile",
            reason=None if launch == "launched" else "automatic_launch_not_proven",
        )
        return
    if state != "succeeded":
        jobs.settle(db, job, "needs_review", reason="automatic_extraction_" + state)
        return
    metadata, candidate = document_evidence.load(config, db, job["client_id"], run)
    if job["phase"] == "review":
        from .automatic_document_continuation import needs_ocr

        if needs_ocr(config, db, job, candidate):
            return
        settings = job["value"]["binding"]["settings"]
        human = document_review.latest(db, run)
        if processing_assessment.checks(settings):
            assessment = automatic_review.document(candidate, metadata, human)
        else:
            if human and any(
                j["verdict"] != "accept" for j in json.loads(human["assessment_json"])["judgments"]
            ):
                raise InvalidInput("human_review_blocks_automatic_publication")
            assessment = processing_assessment.unchecked(
                candidate["source_sha256"],
                candidate["result_sha256"],
                task=run,
                gaps=metadata.get("missing_references", []),
            )
        assessment["task_id"] = run
        job["value"]["review_id"] = automatic_review.issue(config, db, job, assessment)
        if not processing_assessment.saveable(assessment):
            jobs.settle(db, job, "needs_review", reason=assessment["reasons"][0])
        elif not automatic_policy.direct(settings):
            jobs.settle(db, job, "reviewed", "publish")
        else:
            jobs.settle(db, job, "queued", "publish")
        return
    if job["phase"] == "publish":
        jobs.authorize(config, db, job)
        job["value"]["publication"] = document_publication.publish_locked(
            config, db, run, system_review_id=job["value"]["review_id"]
        )
        jobs.settle(db, job, "queued", "index")
        return
    raise InvalidInput("automatic_document_phase_invalid")


def tick(config: Config, *, channel_id: str | None = None, admit_after: float = 0) -> bool:
    # Idle polling must not acquire the writer lease or make offline backup look busy.
    with database.connect(config.state_root) as peek:
        if not peek.execute(
            "SELECT 1 FROM automatic_jobs WHERE state IN ('queued','waiting') LIMIT 1"
        ).fetchone():
            return False
    with database.locked(config), database.connect(config.state_root) as db:
        with database.transaction(db):
            from .channels.services.policy import owned_channels

            job = jobs.claim(
                db,
                channel_id=channel_id,
                admit_after=admit_after,
                excluded_channels=owned_channels(config),
            )
        if job is None:
            return False
        try:
            jobs.authorize(config, db, job)
            if job["phase"] == "model_review" or (
                job["kind"] == "intake_summary" and job["phase"] != "index"
            ):
                pass  # Network execution below runs outside this storage lease.
            elif job["phase"] != "index":
                if job["kind"] == "document" and job["value"].get("chat_archive"):
                    from .automatic_chat import advance as chat_advance

                    chat_advance(config, db, job)
                elif job["kind"] == "document":
                    document_tick(config, db, job)
                elif job["kind"] == "intake":
                    from .automatic_intake import advance as intake_advance

                    intake_advance(config, db, job)
                elif job["kind"] in {"article", "media"}:
                    from .automatic_candidates import advance as candidate_advance

                    candidate_advance(config, db, job)
                elif job["kind"] == "expert":
                    from .automatic_expert import advance

                    advance(config, db, job)
                else:
                    jobs.settle(db, job, "needs_review", reason="automatic_processor_not_ready")
                return True
        except (StorageError, auth.Denied, OSError) as exc:
            # No automatic replay of uncertain filesystem/process side effects.
            from .channels.diagnostics import failure
            from .processing_errors import response

            known = response(exc)
            reason = (
                known[1]["error"]
                if known
                else str(exc)
                if str(exc)
                in {
                    "chat_attachment_projection_pending",
                    "attachment_projection_copy_diverged",
                    "chat_source_changed",
                }
                else "automatic_authorization_changed"
                if isinstance(exc, InvalidInput) and str(exc) == "automatic_authorization_changed"
                else "automatic_stage_requires_reconciliation"
            )
            job["value"]["failure"] = {
                "code": reason,
                "phase": job["phase"],
                "diagnostic": failure(exc)["diagnostic"],
            }
            jobs.settle(
                db,
                job,
                "reconcile" if job["phase"] in {"publish", "extract"} else "needs_review",
                reason=reason,
            )
            return True
    if job["kind"] == "intake_summary" and job["phase"] != "index":
        from .intake_summary import advance as summarize

        summarize(config, job)
        return True
    if job["phase"] == "model_review":
        from .automatic_model import advance as model_advance

        model_advance(config, job)
        return True
    from .note_search_build import build

    try:
        # Index body scanning intentionally runs outside the global storage lease.
        build(config, job["client_id"], job["vault_id"])
        with database.locked(config), database.connect(config.state_root) as db:
            jobs.authorize(config, db, job)
            jobs.settle(db, job, "complete")
            from .intake_summary import after_source

            after_source(config, db, job)
            from .organization.jobs import notify

            publication = job["value"].get("publication") or {}
            target = publication.get("entry_note_path") or "Inbox"
            notify(
                config,
                job["vault_id"],
                "package" if job["kind"] == "expert" else "saved",
                target,
                event_id=job["id"],
            )
    except (StorageError, auth.Denied, OSError):
        with database.locked(config), database.connect(config.state_root) as db:
            jobs.settle(db, job, "needs_review", reason="automatic_index_requires_reconciliation")
    return True


def recover(config: Config) -> None:
    """Preserve known queued stages; interrupted claims require explicit reconciliation."""
    with database.locked(config), database.connect(config.state_root) as db:
        from .channels.services.policy import owned_channels

        owned = owned_channels(config)
        for row in db.execute(
            "SELECT id,value FROM automatic_jobs WHERE state='claimed'"
        ).fetchall():
            value = json.loads(row["value"])
            owner = value.get("intake", {}).get("channel") or value.get("channel_batch", {}).get(
                "channel"
            )
            if owner not in owned:
                db.execute(
                    "UPDATE automatic_jobs SET state='reconcile',"
                    "reason='automatic_interrupted_claim' WHERE id=?",
                    (row["id"],),
                )


class Runtime:
    def __init__(self, config: Config):
        self.config = config
        self.stopped = threading.Event()
        self.thread: threading.Thread | None = None
        self.discovery_thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread is not None:
            return
        recover(self.config)
        from .automatic_placement import recover as placement_recover

        placement_recover(self.config)
        from .vault_merge_recovery import startup

        startup(self.config)
        self.thread = threading.Thread(
            target=self._loop, name="fiber-automatic-ingestion", daemon=True
        )
        self.thread.start()
        from .organization.package_discovery import run as discover

        self.discovery_thread = threading.Thread(
            target=discover,
            args=(self.config, self.stopped),
            name="fiber-book-discovery",
            daemon=True,
        )
        self.discovery_thread.start()

    def _loop(self) -> None:
        while not self.stopped.wait(1):
            try:
                tick(self.config)
                from .automatic_placement import tick as placement_tick

                placement_tick(self.config)
                from .vault_merge_recovery import tick as merge_tick

                merge_tick(self.config)
                from .organization.jobs import tick as organization_tick

                organization_tick(self.config)
            except Exception:
                logging.getLogger(__name__).warning(
                    "automatic ingestion tick requires reconciliation"
                )

    def close(self) -> None:
        self.stopped.set()
        if self.thread is not None:
            self.thread.join(timeout=3)
        if self.discovery_thread is not None:
            self.discovery_thread.join(timeout=3)
