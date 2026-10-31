"""Per-source waits before writes. Unknown exchanges remain a task-wide recovery barrier."""

import time
from typing import Any

from .. import cloud_files, cloud_observation
from ..config import IntegrityError
from . import packages, store

WAITING = {"waiting_file", "waiting_archive"}


def detail(error: Exception, item: Any) -> Any:
    if isinstance(error, cloud_observation.ObservationError):
        return {**error.detail, "code": str(error)}
    if str(error) == "cloud_item_not_downloaded":
        return {"reason": "pending", "path": item["path"], "code": str(error)}
    if str(error) == "maintenance_note_changed":
        return {"reason": "changed", "path": item["path"], "code": str(error)}
    return None


def park(config: Any, job: Any, item: Any, error: Exception) -> bool:
    why = detail(error, item)
    if not why or item.get("intent") or item.get("archive_operation"):
        return False
    if item.get("steps") and item.get("phase") != "members_done":
        return False
    journal = store.get(config, "maintenance_undo", job["id"])
    if journal and journal["state"] == "applying":
        return False
    item.update(state="waiting_file", wait={**why, "since": time.time()})
    item.pop("error", None)
    # Only an ordinary source from this confirmed task can extend omissions.
    # MOCs, attachments, and package members are never treated as missing notes.
    if item["kind"] == "note" and why.get("path") == item["path"] and why["reason"] == "pending":
        job.setdefault("runtime_omissions", {})[item["path"]] = str(error)
    if job.get("runtime_omissions") and job["fixed"].get("unavailable_action") != "defer_links":
        for candidate in job["items"]:
            if candidate["state"] != "done" and candidate.get("target"):
                candidate.update(
                    runtime_archive_deferred=True, deferred_target=candidate["target"], target=None
                )
    job.pop("error_detail", None)
    job["error"] = None
    return True


def settle(job: Any, active: Any) -> None:
    job["completed"] = len(job["items"]) - len(active) + sum(i["state"] == "done" for i in active)
    if all(i["state"] == "done" for i in active):
        job["state"] = "completed"
    elif not any(i["state"] == "pending" for i in active):
        job["state"] = "waiting_file" if any(i["state"] in WAITING for i in active) else "partial"
    else:
        job["state"] = "running"


def resume(config: Any, db: Any, job: Any) -> None:
    from .maintenance_apply import validate_source

    root, _ = packages.context(config, db, job["client_id"], job["vault_id"], write=True)
    job.pop("availability_checked", None)
    with cloud_observation.scope(root):
        for item in job["items"]:
            if item["state"] not in WAITING:
                continue
            try:
                validate_source(config, db, job, item)
            except IntegrityError as error:
                why = detail(error, item)
                if why:
                    item.update(state="waiting_file", wait={**why, "since": time.time()})
                    continue
                raise
            job.get("runtime_omissions", {}).pop(item["path"], None)
            item.update(state="pending")
            item.pop("wait", None)
        if not job.get("runtime_omissions"):
            for item in job["items"]:
                if item.pop("runtime_archive_deferred", False):
                    item["target"] = item.pop("deferred_target")
                    if item["state"] == "waiting_archive":
                        item["state"] = "pending"
        job["error"] = None


def pending_sources(job: Any) -> list[str]:
    return sorted(
        {
            i["wait"]["path"]
            for i in job["items"]
            if i.get("wait", {}).get("reason") == "pending" and i["wait"].get("path")
        }
    )


def prepare_scope(config: Any, db: Any, job: Any) -> bool:
    """One bounded metadata pass per explicit run, not an idle cloud-folder poll."""
    root, scope = packages.context(config, db, job["client_id"], job["vault_id"], write=True)
    checked = set(job.get("availability_checked", []))
    pending = [
        i
        for i in job["items"]
        if i["state"] == "pending"
        and i["kind"] == "note"
        and i["entity"] not in checked
        and i.get("phase") != "archived"
        and not i.get("archive_operation")
        and not i.get("intent")
    ]
    for item in pending[:100]:
        if not scope.allows(item["path"]):
            raise IntegrityError("maintenance_authorization_or_policy_changed")
        try:
            cloud_files.require_local(root / item["path"])
        except cloud_observation.ObservationError as error:
            if not park(config, job, item, error):
                raise
        checked.add(item["entity"])
    job["availability_checked"] = sorted(checked)
    return len(pending) <= 100
