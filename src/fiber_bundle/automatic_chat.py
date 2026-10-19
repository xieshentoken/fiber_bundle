"""Native chat export admission and reviewed publication through the existing automatic queue."""

import json
import sqlite3
from pathlib import PurePosixPath
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from . import (
    archive_members,
    attachment_projection,
    auth,
    automatic_jobs,
    automatic_policy,
    automatic_review,
    chat_archive,
    chat_markdown,
    operations,
    processing_assessment,
    publication_file_io,
    publication_layout,
    publication_locations,
    vault_directories,
)
from .config import Config, IntegrityError, InvalidInput
from .inputs import canonical
from .objects import digest


def enqueue(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vault: str,
    receipt: dict[str, Any],
    asset: sqlite3.Row,
) -> str | None:
    raw = operations.original(
        config, db, client, vault, receipt["source_version_id"], asset["asset_id"], 12 * 1024 * 1024
    )
    try:
        chat_archive.inspect(raw)
    except InvalidInput as exc:
        if str(exc) in {"chat_archive_export_unrecognized", "chat_archive_other_purpose"}:
            return None
        raise
    source = str(uuid5(NAMESPACE_URL, "fiber-chat:" + vault + ":" + digest(raw)))
    previous = db.execute(
        "SELECT id FROM automatic_jobs WHERE kind='document' AND source_id=? "
        "AND state='needs_review' AND reason='chat_attachment_missing'",
        (source,),
    ).fetchone()
    continuation: dict[str, Any] = {}
    if (
        previous
        and db.execute("SELECT 1 FROM automatic_reviews WHERE job_id=?", (previous[0],)).fetchone()
    ):
        # A rejected review is immutable and unique per job. Continue under a stable
        # new identity only for the newly permitted missing-image case; never replay a publication.
        updated = load(config, db, automatic_jobs.get(db, previous[0]))
        if updated["missing_images"] and not updated["gaps"]:
            continuation["supersedes_job"] = previous[0]
            source = str(
                uuid5(NAMESPACE_URL, "fiber-chat-missing-images-v1:" + vault + ":" + digest(raw))
            )
    return automatic_jobs.enqueue(
        config,
        db,
        client,
        vault,
        "document",
        source,
        {
            "chat_archive": {
                "receipt_id": receipt["job_id"],
                "version_id": receipt["source_version_id"],
                "asset_id": asset["asset_id"],
                "name": asset["original_name"],
                "sha256": digest(raw),
            },
            **continuation,
        },
    )


def projected(
    config: Config, db: sqlite3.Connection, job: dict[str, Any], asset: str, version: str
) -> str:
    identity = str(uuid5(NAMESPACE_URL, f"fiber-original:{job['vault_id']}:{version}:{asset}"))
    row = db.execute(
        "SELECT state,payload FROM vault_operations WHERE id=? AND kind='attachment_projection'",
        (identity,),
    ).fetchone()
    if row is None:
        return str(
            attachment_projection.project(
                config, db, job["client_id"], job["vault_id"], version, asset
            )["path"]
        )
    if row["state"] != "published":
        raise InvalidInput("chat_attachment_projection_pending")
    value = json.loads(row["payload"])
    root, scope, _ = vault_directories.vault_root(config, db, job["vault_id"])
    auth.grant(db, job["client_id"], job["vault_id"], "original")
    auth.grant(db, job["client_id"], job["vault_id"], "add")
    raw = operations.original(
        config, db, job["client_id"], job["vault_id"], version, asset, attachment_projection.LIMIT
    )
    if not scope.allows(value["path"]):
        raise auth.Denied(404)
    saved, key = publication_file_io.read(root, value["path"])
    # Existing verified attachments retain their original path even after a layout update.
    if (
        value["root_key"] != vault_directories.key(root)
        or not scope.allows(value["path"])
        or key[:2] != value["claim"]
        or saved != raw
        or digest(raw) != value["sha256"]
    ):
        raise IntegrityError("attachment_projection_copy_diverged")
    return str(value["path"])


def load(config: Config, db: sqlite3.Connection, job: dict[str, Any]) -> dict[str, Any]:
    source = job["value"]["chat_archive"]
    raw = operations.original(
        config,
        db,
        job["client_id"],
        job["vault_id"],
        source["version_id"],
        source["asset_id"],
        12 * 1024 * 1024,
    )
    if digest(raw) != source["sha256"]:
        raise IntegrityError("chat_source_changed")
    assets = [
        dict(r)
        for r in db.execute(
            "SELECT va.asset_id AS id,va.original_name,va.media_type,a.sha256 "
            "FROM version_assets va "
            "JOIN assets a ON a.id=va.asset_id WHERE va.version_id=?",
            (source["version_id"],),
        )
    ]
    mapping = archive_members.project(raw, assets)
    paths = {
        m["decoded_path"]: projected(config, db, job, m["asset_id"], source["version_id"])
        for m in mapping["members"]
        if m["asset_id"] and not m["uncertain"]
    }
    return chat_markdown.render(raw, PurePosixPath(source["name"]).stem, paths)


def advance(config: Config, db: sqlite3.Connection, job: dict[str, Any]) -> None:
    automatic_jobs.authorize(config, db, job)
    captured = load(config, db, job)
    settings = job["value"]["binding"]["settings"]
    source, result = captured["source_sha256"], captured["result_sha256"]
    if job["phase"] == "extract":
        gaps = captured["gaps"]
        missing = captured["missing_images"]
        assessment = {
            "task_id": job["source_id"],
            "source_sha256": source,
            "result_sha256": result,
            "decision": "uncertain" if gaps else "accept",
            "reasons": gaps
            or [
                "chat_text_saved_with_missing_images"
                if missing
                else "complete_structurally_verified_chat_export"
            ],
            "units": [
                {"unit_id": p, "decision": "uncertain" if gaps else "accept"}
                for p in captured["text_members"]
            ],
            "unverified_dimensions": ["chat_identity", "platform_completeness", "human_review"],
        }
        if missing:
            assessment.update(
                completeness_status="partial",
                known_gaps=[*gaps, "chat_image_missing"],
                save_basis="chat_text_with_missing_image_markers",
            )
        if not processing_assessment.checks(settings):
            assessment = processing_assessment.unchecked(
                source,
                result,
                task=job["source_id"],
                gaps=[*gaps, *(["chat_image_missing"] if missing else [])],
            )
        job["value"]["review_id"] = automatic_review.issue(config, db, job, assessment)
        job["value"]["references"] = captured["references"]
        job["value"]["missing_images"] = missing
        automatic_jobs.settle(
            db,
            job,
            "needs_review"
            if not processing_assessment.saveable(assessment)
            else "queued"
            if automatic_policy.direct(settings)
            else "reviewed",
            "publish",
            gaps[0] if gaps else None,
        )
        return
    if job["phase"] != "publish":
        raise InvalidInput("automatic_document_phase_invalid")
    review = automatic_review.credential(
        config, db, job["value"]["review_id"], "document", job["source_id"], source, result
    )
    files = captured["files"]
    publication_layout.prepare(config, db, "intake", job["source_id"], job["vault_id"], files)
    location = publication_locations.get(db, "intake", job["source_id"])
    if location and not location["plan"]["claims"]:
        location["plan"]["intake_source"] = {
            **job["value"]["chat_archive"],
            "kind": "chat_archive",
            "source_version_id": job["value"]["chat_archive"]["version_id"],
            "review_id": review["id"],
            "review_actor": review["actor"],
            "review_type": "system",
        }
        db.execute(
            "UPDATE publication_locations SET value=? WHERE kind='intake' AND publication_id=?",
            (canonical(location["plan"]), job["source_id"]),
        )
    job["value"]["publication"] = publication_layout.publish(
        config, db, "intake", job["source_id"], job["client_id"], files
    )
    automatic_jobs.settle(db, job, "queued", "index")
