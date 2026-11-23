"""Persistent explicit Draft import intents; HTTP never copies files or invokes a model."""

import time
from typing import Any

from .. import auth
from ..config import IntegrityError, InvalidInput
from . import catalog, packages, store
from . import draft_import_preview as preview

TABLE = "draft_imports"
BUSY = {"queued", "working"}


def save(config: Any, task: dict[str, Any]) -> None:
    task["updated_at"] = time.time()
    store.put(config, TABLE, task["id"], task["vault_id"], task)


def owned(config: Any, db: Any, identity: str, client: str, vault: str) -> dict[str, Any]:
    packages.context(config, db, client, vault, write=True)
    task = store.get(config, TABLE, identity)
    if not task or (task["client_id"], task["vault_id"]) != (client, vault):
        raise auth.Denied(404)
    return dict(task)


def create(config: Any, db: Any, value: dict[str, Any]) -> dict[str, Any]:
    fixed = preview.target(config, db, value["client_id"], value["vault_id"])
    submission = value["submission_id"]
    prior = store.get(config, TABLE, submission)
    if prior:
        prior = owned(config, db, submission, value["client_id"], value["vault_id"])
        if (prior["source_path"], prior.get("folder", "")) != (
            value["source_path"],
            value["folder"],
        ):
            raise IntegrityError("draft_submission_conflict")
        return public(prior)
    # Root is inspected by the background validation phase, not a long HTTP request.
    task = {
        **value,
        "id": submission,
        "target_evidence": fixed,
        "state": "queued",
        "phase": "validation",
        "created_at": time.time(),
        "confirmed": False,
        "moc_ids": [],
        "moc_results": [],
        "copied_bytes": 0,
        "copied_files": 0,
        "error": None,
        "index_state": "not_started",
    }
    save(config, task)
    return public(task)


def confirm(config: Any, db: Any, task: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    if task["confirmed"]:
        if task["preview_sha256"] != value["preview_sha256"] or task["moc_ids"] != value["moc_ids"]:
            raise IntegrityError("draft_confirmation_conflict")
        return public(task)
    if (
        task["state"] != "preview"
        or task["preview_expires"] < time.time()
        or task["preview_sha256"] != value["preview_sha256"]
    ):
        raise InvalidInput("draft_preview_expired_or_changed")
    if value["candidate_acknowledged"] is not True:
        raise InvalidInput("draft_candidate_acknowledgement_required")
    preview.recheck_target(config, db, task)
    _, mocs, _ = catalog.snapshot(config, db, task["client_id"], task["vault_id"])
    selected = list(dict.fromkeys(value["moc_ids"]))
    expected = {m["id"]: m for m in task["mocs"]}
    current = {m["id"]: m for m in mocs}
    if any(
        mid not in expected
        or mid not in current
        or expected[mid]["sha256"] != current[mid]["sha256"]
        for mid in selected
    ):
        raise IntegrityError("draft_moc_changed")
    task.update(confirmed=True, moc_ids=selected, state="queued", phase="copy", error=None)
    save(config, task)
    return public(task)


def action(config: Any, task: dict[str, Any], operation: str) -> dict[str, Any]:
    if operation == "cancel":
        if task["state"] == "completed":
            return public(task)
        task["cancel_requested"] = True
        if task["state"] != "working":
            task["state"] = "cancelled"
    elif operation == "resume":
        if task["state"] not in {"failed", "interrupted", "cancelled", "partial"}:
            raise InvalidInput("draft_import_not_resumable")
        task.update(cancel_requested=False, state="queued", error=None)
        if not task["confirmed"]:
            task["phase"] = "validation"
    else:
        raise InvalidInput("draft_import_action_invalid")
    save(config, task)
    return public(task)


def public(task: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "client_id",
        "vault_id",
        "source_path",
        "folder",
        "state",
        "phase",
        "error",
        "confirmed",
        "created_at",
        "updated_at",
        "relative_path",
        "preview_sha256",
        "mocs",
        "moc_ids",
        "moc_results",
        "total_bytes",
        "copied_bytes",
        "copied_files",
        "free_bytes",
        "required_bytes",
        "reuse",
        "ignored_files",
        "index_state",
    )
    result = {k: task[k] for k in keys if k in task}
    package = task.get("package")
    if package:
        result["package"] = {**packages.public(package), "file_count": len(package["files"])}
    if "target_evidence" in task:
        result["target_path"] = (
            task["target_evidence"]["root"]
            + "/"
            + task.get("relative_path", task["target_evidence"]["book_dir"])
        )
    return result


def pending(config: Any, vault: str) -> set[str]:
    return {
        t["package"]["module_id"]
        for t in store.rows(config, TABLE, vault)
        if t.get("confirmed")
        and t.get("moc_ids")
        and t["state"] != "completed"
        and t.get("package")
    }
