"""Optional, independently recoverable enrichment of an already published source."""

from typing import Any

from . import automatic_jobs as jobs
from . import (
    automatic_policy,
    database,
    execution,
    execution_ledger,
    pi_processing,
    publication_layout,
)
from .config import Config, InvalidInput, StorageError
from .inputs import canonical
from .objects import digest


def enqueue(
    config: Config, db: Any, channel: str, batch: str, index: int, value: dict[str, Any]
) -> str | None:
    from .channels import intake_repository

    entry, _ = intake_repository.channel(config, channel)
    settings = automatic_policy.get(db, value["target_vault_id"])["settings"]
    if not settings.get("extra_summary"):
        return None
    link = value["links"][index]
    if (link.get("publication") or {}).get("state") != "published":
        raise InvalidInput("intake_summary_source_not_published")
    model = automatic_policy.processing_route(config, settings)
    return jobs.enqueue(
        config,
        db,
        entry["value"]["client_id"],
        value["target_vault_id"],
        "intake_summary",
        f"{channel}:{batch}:{index}",
        {
            "model_sha256": model.digest,
            "summary_source": {
                "channel": channel,
                "batch": batch,
                "index": index,
                "sha256": link["files"]["note.md"]["sha256"],
            },
        },
    )


def after_source(config: Config, db: Any, job: dict[str, Any]) -> None:
    if job["kind"] != "intake" or not job["value"]["binding"]["settings"].get("extra_summary"):
        return
    from .channels import intake_reviews, store

    context = job["value"]["intake"]
    try:
        with store.readonly(config) as side:
            _, _, value, _ = intake_reviews.load(
                config, db, side, context["channel"], context["batch"], context["index"]
            )
        enqueue(config, db, context["channel"], context["batch"], context["index"], value)
    except (StorageError, OSError):
        # The source and its index are already committed. Enrichment admission
        # failure cannot turn successful source publication back into a failure.
        job["value"]["summary_admission"] = "failed"
        jobs.settle(db, job, "complete")


def load(config: Config, db: Any, job: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    from .channels import intake_reviews, store

    jobs.authorize(config, db, job)
    context = job["value"]["summary_source"]
    with store.readonly(config) as side:
        _, _, value, files = intake_reviews.load(
            config, db, side, context["channel"], context["batch"], context["index"]
        )
    link = value["links"][context["index"]]
    if (link.get("publication") or {}).get("state") != "published":
        raise InvalidInput("intake_summary_source_not_published")
    if digest(files["note.md"]) != context["sha256"]:
        raise InvalidInput("intake_summary_source_changed")
    return link, files["note.md"]


def advance(config: Config, job: dict[str, Any]) -> None:
    """Network runs outside the writer lease. Every committed step rechecks ownership."""
    try:
        _advance(config, job)
    except (StorageError, OSError) as error:
        from .model_configuration.errors import MESSAGES as model_errors
        from .processing_errors import MESSAGES

        reason = str(error)
        with database.locked(config), database.connect(config.state_root) as db:
            jobs.settle(
                db,
                job,
                "needs_review",
                reason=(
                    reason
                    if reason in MESSAGES or reason in model_errors
                    else "intake_summary_failed"
                ),
            )


def _advance(config: Config, job: dict[str, Any]) -> None:
    from .channels.intake_pi import text_chunks

    with database.locked(config), database.connect(config.state_root) as db:
        link, original = load(config, db, job)
        policy = automatic_policy.processing_route(config, job["value"]["binding"]["settings"])
        if policy.digest != job["value"]["model_sha256"]:
            raise InvalidInput("intake_processing_configuration_changed")
    chunks = text_chunks(original.decode(), min(policy.max_request_bytes // 4, 16000))
    if not chunks or len(chunks) > 1000:
        raise InvalidInput("intake_processing_source_capacity")
    results = []
    run_policy = pi_processing.execution_policy(policy)
    for number, text in enumerate(chunks):
        with database.locked(config), database.connect(config.state_root) as db:
            load(config, db, job)
            runs = job["value"].setdefault("runs", {})
            rid = runs.get(str(number))
            if rid is None:
                rid = pi_processing.prepare(
                    config,
                    db,
                    run_policy,
                    job["client_id"],
                    job["vault_id"],
                    link["receipt"]["source_version_id"],
                    "article",
                    digest(original),
                    [{"id": f"capture:{number}", "text": text}],
                    allow_cloud=job["value"]["binding"]["settings"]["allow_model_source_content"],
                )
                runs[str(number)] = rid
                jobs.settle(db, job, "claimed")
            state = execution_ledger.get_run(db, rid)["state"]
        if state in {"queued", "waiting_budget"}:
            execution.execute(config, run_policy, rid, "fixed processing skill")
        with database.locked(config), database.connect(config.state_root) as db:
            load(config, db, job)
            run = execution_ledger.get_run(db, rid)
            if run["state"] != "succeeded":
                jobs.settle(
                    db,
                    job,
                    "needs_review",
                    reason=(
                        "intake_processing_budget_required"
                        if run["state"] == "waiting_budget"
                        else run["error_code"] or "intake_summary_failed"
                    ),
                )
                return
            try:
                results.append(pi_processing.result(config, db, rid))
            except (StorageError, ValueError) as error:
                rejected = job["value"].setdefault("rejected_runs", [])
                if rid not in rejected:
                    rejected.append(rid)
                raise InvalidInput("intake_summary_invalid_result") from error
    source_path = link["publication"].get("entry_note_path", "")
    title = publication_layout.title({"note.md": original}, "笔记")
    body = f"# {title}-分析\n\n模型分析草稿，非独立证据，未独立核验。\n\n"
    if source_path:
        body += f"原文：[[{source_path}]]\n\n"
    for result in results:
        body += result["summary"] + "\n\n"
        if result["gaps"]:
            body += "分析局限：\n" + "\n".join("- " + g for g in result["gaps"]) + "\n\n"
    files = {"note.md": body.encode()}
    with database.locked(config), database.connect(config.state_root) as db:
        load(config, db, job)
        location = publication_layout.prepare(
            config, db, "intake", job["id"], job["vault_id"], files
        )
        if location is None:
            raise InvalidInput("intake_summary_layout_unavailable")
        if location["state"] == "prepared" and not location["plan"]["claims"]:
            location["plan"]["intake_source"] = {
                "source_version_id": link["receipt"]["source_version_id"],
                "source_url": link.get("url"),
                "analysis_sha256": digest(files["note.md"]),
                "review_type": "system",
                "review_actor": "system_model",
                "review_id": job["id"],
                "processing_gaps": [],
            }
            db.execute(
                "UPDATE publication_locations SET value=? WHERE kind='intake' AND publication_id=?",
                (canonical(location["plan"]), job["id"]),
            )
        jobs.authorize(config, db, job)
        job["value"]["publication"] = publication_layout.publish(
            config, db, "intake", job["id"], job["client_id"], files
        )
        jobs.settle(db, job, "queued", "index")
