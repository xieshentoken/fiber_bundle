"""Durable delays only for read-only preflight and already-committed index work."""

import time
from typing import Any

from . import automatic_jobs as jobs
from .config import Config, StorageError

CLOUD = frozenset(
    {
        "cloud_item_not_downloaded",
        "cloud_item_pending_stale_or_conflicted",
        "cloud_status_check_timeout",
        "cloud_metadata_unavailable",
    }
)
DELAYS = (60, 300, 900, 1800)


def defer(db: Any, job: dict[str, Any], reason: str) -> None:
    prior = job["value"].get("availability_wait", {})
    attempts = int(prior.get("attempts", 0))
    job["value"]["availability_wait"] = {
        "attempts": attempts + 1,
        "next_check": time.time() + DELAYS[min(attempts, len(DELAYS) - 1)],
        "phase": job["phase"],
    }
    jobs.settle(db, job, "waiting", reason=reason)


def preflight(config: Config, db: Any, job: dict[str, Any]) -> bool:
    # This call cannot publish or launch a process. Failures after this boundary
    # retain the existing explicit reconciliation behavior.
    try:
        jobs.authorize(config, db, job)
    except StorageError as error:
        if str(error) not in CLOUD:
            raise
        defer(db, job, "automatic_waiting_for_local_folder")
        return False
    if job["phase"] == "publish":
        from .publication_blockers import pending

        blockers = pending(db, job["vault_id"])
        if blockers:
            job["value"]["publication_blockers"] = blockers
            defer(db, job, "placement_reconciliation_required")
            return False
        job["value"].pop("publication_blockers", None)
    if job["phase"] != "index":
        job["value"].pop("availability_wait", None)
    return True


def index_ready(config: Config, db: Any, job: dict[str, Any]) -> bool | None:
    import json

    from . import auth, automatic_job_status, note_index, note_search_cache, vaults
    from .inputs import canonical
    from .vault_sync import index_state, maintenance, store

    vid = job["vault_id"]
    if store.library(config, vid) is None:
        return None
    saved = automatic_job_status.publication(db, job)
    path = saved.get("entry_note_path")
    if not path or saved.get("state") != "published":
        raise StorageError("automatic_committed_publication_required")
    vault = auth.grant(db, job["client_id"], vid, "query")
    try:
        index_state.token(config, db, vault)
        with note_search_cache.connection(config, vid) as index:
            found = (
                index.execute("SELECT metadata FROM notes WHERE path=?", (path,)).fetchone()
                if index
                else None
            )
        if found:
            current = note_index.read_note(
                vaults.checked_vault(config, vault), path, note_index.Limits().note_bytes
            )
            value = json.loads(found[0])
            if value["sha256"] == current.sha256 and canonical(value["file_key"]) == canonical(
                current.file_key
            ):
                job["value"].pop("availability_wait", None)
                return True
    except (note_search_cache.IndexUnavailable, StorageError):
        pass
    if not job["value"].get("index_observation_requested"):
        maintenance.dirty(config, vid, [path.rsplit("/", 1)[0] if "/" in path else path])
        job["value"]["index_observation_requested"] = True
    defer(db, job, "automatic_saved_index_updating")
    return False
