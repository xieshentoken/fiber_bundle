"""New channel originals reuse the GUI document pipeline; archive-only remains archive-only."""

import sqlite3
from typing import Any

from . import automatic_jobs, automatic_policy, source_import_existing
from .config import Config, StorageError


def admit(
    config: Config, db: sqlite3.Connection, client: str, vault: str, value: dict[str, Any]
) -> None:
    imports: list[dict[str, Any]] = []
    admitted = False
    for receipt in value["receipts"]:
        assets = db.execute(
            "SELECT va.*,a.sha256 FROM version_assets va JOIN assets a ON a.id=va.asset_id "
            "WHERE va.version_id=? AND va.role!='event' ORDER BY va.ordinal",
            (receipt["source_version_id"],),
        ).fetchall()
        zipped = any(a["media_type"] == "application/zip" for a in assets)
        for asset in assets:
            if zipped and asset["original_name"].startswith("zip-member-"):
                continue
            # Links are captured separately; raw URLs are not substituted for their article bodies.
            if value.get("links") and asset["media_type"] in {"text/plain", "text/uri-list"}:
                continue
            if not admitted:
                if automatic_policy.binding(config, db, vault, client, "document") is None:
                    return
                admitted = True
            try:
                if asset["media_type"] == "application/zip":
                    from .automatic_chat import enqueue

                    job = enqueue(config, db, client, vault, receipt, asset)
                    if job:
                        imports.append({"job_id": job, "kind": "chat_archive"})
                        continue
                imported = source_import_existing.create(
                    config,
                    db,
                    client,
                    vault,
                    receipt["job_id"],
                    asset["asset_id"],
                    "ordinary",
                    {"kind": "channel_original"},
                )
                job = automatic_jobs.enqueue(config, db, client, vault, "document", imported["id"])
                imports.append({"import_id": imported["id"], "job_id": job})
            except (StorageError, OSError):
                imports.append(
                    {
                        "asset_id": asset["asset_id"],
                        "state": "needs_review",
                        "reason": "automatic_source_processor_not_ready",
                    }
                )
    value["automatic_imports"] = imports
    if value.get("channel_batch"):
        for imported in imports:
            if imported.get("job_id"):
                current = automatic_jobs.get(db, imported["job_id"])
                if "channel_batch" not in current["value"]:
                    current["value"]["channel_batch"] = value["channel_batch"]
                    automatic_jobs.settle(db, current, current["state"])
