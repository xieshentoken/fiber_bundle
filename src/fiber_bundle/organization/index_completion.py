"""Join the Vault observer's index queue without re-executing organization work."""

import time
from typing import Any

from .. import database
from ..config import Config, InvalidInput
from ..note_search_cache import IndexUnavailable
from ..vault_sync import commands, presentation
from . import store


def advance(config: Config, job: Any) -> None:
    updated = dict(job)
    try:
        status = presentation.index_status(config, job["vault_id"])
        if status is None:
            # Compatibility for installations without the persistent observer.
            from ..note_search_build import build

            build(config, job["client_id"], job["vault_id"])
            updated.update(index_state="ready", index_error=None)
        else:
            after = job.get("updated_at") or job.get("created_at", 0)
            if status["state"] == "ready" and (status.get("built_at") or 0) >= after:
                updated.update(index_state="ready", index_error=None)
            elif (job.get("index_requested_at") or 0) < after or not job.get("index_request_id"):
                request = commands.submit(config, job["client_id"], job["vault_id"])
                updated.update(
                    index_request_id=request["job_id"],
                    index_requested_at=time.time(),
                    index_error=None,
                )
            elif status["state"] in {"failed", "partial"}:
                updated.update(
                    index_state="failed",
                    index_error=(status["sync"].get("error") or "note_index_coverage_incomplete"),
                )
            # Pending/building/waiting remain pending. Only the observer builds.
    except (IndexUnavailable, InvalidInput) as error:
        if str(error) not in {"note_index_update_required", "execution already owned"}:
            updated.update(
                index_state="failed", index_error=type(error).__name__ + ": " + str(error)[:120]
            )
    except Exception as error:
        from ..vault_relocation.guard import paused

        if paused(config, job["vault_id"]):
            return
        updated.update(
            index_state="failed", index_error=type(error).__name__ + ": " + str(error)[:120]
        )
    if updated != job:
        with database.locked(config):
            # A concurrent pause/retry/action owns the newer task state.
            if store.get(config, "jobs", job["id"]) == job:
                store.put(config, "jobs", job["id"], job["vault_id"], updated)
