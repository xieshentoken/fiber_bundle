"""Acknowledge an already current index without replaying publication or follow-up work."""

import json
import sqlite3
from typing import Any

from . import auth, automatic_job_status, automatic_jobs, note_index, publication_locations, vaults
from . import note_search_cache as cache
from .config import Config, IntegrityError


def acknowledge(
    config: Config, db: sqlite3.Connection, job: dict[str, Any]
) -> dict[str, Any] | None:
    """Caller holds the storage lease and admin authorization; no work is enqueued.

    Frozen processing policy is deliberately not reused for this observation. Current
    access and committed publication/index evidence are required instead. Changed
    policy never authorizes replay of extraction, publication, summary or organization.
    """
    if job["phase"] != "index" or job["state"] not in {"needs_review", "reconcile"}:
        return None
    if job["value"].get("pending_model") or automatic_job_status.successor(db, job):
        return None
    location = automatic_job_status.location(db, job)
    if (
        not location
        or location["vault_id"] != job["vault_id"]
        or not publication_locations.committed(db, location)
    ):
        return None
    vault = auth.grant(db, job["client_id"], job["vault_id"], "query")
    for permission in ("original", "add"):
        auth.grant(db, job["client_id"], job["vault_id"], permission)
    root = vaults.checked_vault(config, vault)
    with cache.connection(config, job["vault_id"]) as index:
        if index is None:
            return None
        generation = index.execute("SELECT * FROM generation").fetchone()
        if (
            generation is None
            or generation["state"] != "ready"
            or cache.fingerprint(config, db, vault) != generation["fingerprint"]
        ):
            return None
        managed, _ = publication_locations.inventory(db, job["vault_id"])
        targets = {
            path: item
            for path, item in managed.items()
            if (item["kind"], item["publication_id"])
            == (location["kind"], location["publication_id"])
        }
        if location["entry_note_path"] not in targets:
            return None
        evidence = []
        for path, item in sorted(targets.items()):
            cached = index.execute("SELECT metadata FROM notes WHERE path=?", (path,)).fetchone()
            if cached is None:
                return None
            metadata = json.loads(cached[0])
            current = note_index.read_note(root, path, note_index.Limits().note_bytes)
            if (
                metadata.get("relative_path") != path
                or metadata.get("identity") != item["identity"]
                or metadata.get("file_key") != item["file_key"]
                or list(current.file_key or ()) != item["file_key"]
                or metadata.get("sha256") != current.sha256
            ):
                raise IntegrityError("automatic_index_source_changed")
            evidence.append({"path": path, "sha256": current.sha256})
        if cache.fingerprint(config, db, vault) != generation["fingerprint"]:
            raise IntegrityError("automatic_index_source_changed")
        proof = {"fingerprint": generation["fingerprint"], "notes": evidence}
    job["value"].setdefault("failure_history", []).append(
        {
            "phase": "index",
            "reason": job.get("reason"),
            "failure": job["value"].pop("failure", None),
        }
    )
    job["value"]["index_reconciliation"] = proof
    automatic_jobs.settle(db, job, "complete")
    return {
        "state": "complete",
        "phase": "index",
        "existing_index_verified": True,
        "new_model_attempt": False,
        "post_processing_started": False,
    }
