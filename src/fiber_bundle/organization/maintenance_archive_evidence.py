"""Read-only original-operation and undo evidence for safe archive deferral/closing."""

import copy
import json
import time
from dataclasses import asdict
from typing import Any

from .. import (
    note_identity_store,
    note_placement_execution,
    publication_file_io,
    publication_links,
    publication_locations,
)
from ..config import IntegrityError, InvalidInput
from ..inputs import canonical
from ..objects import digest
from . import maintenance_membership_validation, packages, store


def untouched(
    config: Any,
    db: Any,
    job: Any,
    item: Any,
    *,
    retained: bool = False,
    require_blocked: bool = False,
) -> Any:
    """Verify original evidence without executing, cancelling or editing the operation."""
    operation = item.get("archive_operation")
    row = db.execute(
        "SELECT kind,state,payload FROM vault_operations WHERE id=? AND vault_id=?",
        (operation, job["vault_id"]),
    ).fetchone()
    if row is None or row["kind"] != "note_placement" or row["state"] != "prepared":
        raise IntegrityError("maintenance_close_recovery_required")
    value = json.loads(row["payload"])
    if not isinstance(value, dict):
        raise IntegrityError("maintenance_close_recovery_required")
    if (
        not job.get("confirmed")
        or item.get("archive_kind") != "managed"
        or item.get("phase") != "members_done"
        or item.get("intent")
        or value.get("client_id") != job["client_id"]
        or value.get("vault_id") != job["vault_id"]
        or value.get("source_root") != item["path"]
        or value.get("target_root") != item.get("target")
        or value.get("action") not in {"archive", "expert_archive"}
        or value.get("association")
        or value.get("association_applied")
        or value.get("move_applied")
        or value.get("exchanges")
        or value.get("metadata_committed")
        or not value.get("source_files")
    ):
        raise IntegrityError("maintenance_close_recovery_required")
    expiry = value.get("expires_at")
    if expiry is not None and (not isinstance(expiry, (int, float)) or isinstance(expiry, bool)):
        raise IntegrityError("maintenance_archive_binding_changed")
    reason = (
        "maintenance_archive_link_review_required"
        if value.get("unresolved_links")
        else "placement_preview_expired"
        if expiry is not None and time.time() > expiry
        else None
    )
    if require_blocked and not reason:
        raise InvalidInput("maintenance_archive_deferral_required")
    from .maintenance import validate

    validate(config, db, job, sources=False)
    root, allowed = packages.context(config, db, job["client_id"], job["vault_id"], write=True)
    moves = value.get("moves", {})
    source_files = value.get("source_files", {})
    if (
        not isinstance(moves, dict)
        or not isinstance(source_files, dict)
        or set(moves) != set(source_files)
    ):
        raise IntegrityError("maintenance_archive_binding_changed")
    source, target = value["source_root"], value["target_root"]
    if any(
        not isinstance(path, str)
        or not isinstance(destination, str)
        or not (path == source or item["kind"] == "expert" and path.startswith(source + "/"))
        or destination != target + path[len(source) :]
        for path, destination in moves.items()
    ):
        raise IntegrityError("maintenance_archive_binding_changed")
    updates = value.get("updates")
    if not isinstance(updates, list) or any(
        not isinstance(update, dict)
        or not isinstance(update.get("path"), str)
        or update.get("target") != moves.get(update["path"], update["path"])
        for update in updates
    ):
        raise IntegrityError("maintenance_archive_binding_changed")
    for path in set(moves) | set(moves.values()) | {u["path"] for u in updates}:
        if not allowed.allows(path):
            raise IntegrityError("maintenance_archive_scope_changed")
    receipt = item.get("archive_deferral") if retained else None
    payload_digest = digest(row["payload"].encode())
    if retained:
        if (
            not receipt
            or receipt.get("operation") != operation
            or receipt.get("payload_sha256") != payload_digest
        ):
            raise IntegrityError("maintenance_close_recovery_required")
        # Later independent items can legitimately advance the same Map and
        # registrations. The receipt proves the unchanged original operation;
        # current task membership/source evidence is still checked below.
    else:
        note_placement_execution.check(config, db, value)
    location = publication_locations.get(db, value["kind"], value["publication_id"])
    if not location or location["revision"] != value["location_revision"]:
        raise IntegrityError("maintenance_archive_binding_changed")
    current = [asdict(r) for r in note_identity_store.records(config, job["vault_id"])]
    registrations = value["registrations"]
    if retained:
        current = [
            r
            for r in current
            if r["relative_path"] in source_files or r["identity"] == item["entity"]
        ]
        registrations = [
            r
            for r in registrations
            if r["relative_path"] in source_files or r["identity"] == item["entity"]
        ]
    if canonical(current) != canonical(registrations):
        raise IntegrityError("maintenance_archive_binding_changed")
    if (root / value["target_root"]).exists() or (root / value["target_root"]).is_symlink():
        raise IntegrityError("maintenance_archive_target_conflict")
    if item["kind"] == "expert":
        paths = {
            value["source_root"] + "/" + p
            for p in publication_file_io.tree(root, value["source_root"], allowed)
        }
        if paths != set(value["source_files"]):
            raise IntegrityError("maintenance_package_members_changed")
    observed = []
    for path, expected in value["source_files"].items():
        raw, key = publication_file_io.read(root, path)
        if digest(raw) != expected["sha256"] or key != expected["file_key"]:
            raise IntegrityError("maintenance_note_changed")
        observed.append([path, digest(raw), key])
    # Before the explicit receipt, every planned exchange must still be at its
    # original inode/revision. Afterwards the untouched operation remains inert;
    # later units may have legitimately rewritten its Map links.
    if not retained:
        for update in value["updates"]:
            raw, key = publication_file_io.read(root, update["path"])
            if digest(raw) != update["before_sha256"] or key != update["file_key"]:
                raise IntegrityError("maintenance_note_changed")
            observed.append([update["path"], digest(raw), key])
    maintenance_membership_validation.validate_item(config, db, job, item, complete=True)
    return {
        "operation": operation,
        "payload_sha256": payload_digest,
        "source_files": observed,
        "links": receipt["links"] if retained and receipt else link_evidence(root, allowed, value),
        "reason": reason,
        "path": item["path"],
        "target": item["target"],
    }


def journal_proof(config: Any, db: Any, job: Any) -> Any:
    """Read-only outcome check shared by deferral and safe task closing."""
    from . import maintenance_undo, maintenance_undo_files, maintenance_undo_seal

    original = store.get(config, "maintenance_undo", job["id"])
    if not original or original["state"] not in {"ready", "applying"}:
        raise IntegrityError("maintenance_close_recovery_required")
    journal = copy.deepcopy(original)
    root, _ = packages.context(config, db, job["client_id"], job["vault_id"], write=True)
    if journal["state"] == "applying":
        known = {p["id"] for p in journal["plans"]}
        owned = [
            p
            for p in maintenance_undo.plans(config, job)
            if p.plan_id not in journal["unit_plans"]
            or ("unit_paths" not in journal and p.plan_id in known)
        ]
        if any(p.status == "pending" for p in owned):
            raise IntegrityError("maintenance_close_recovery_required")
        maintenance_undo_seal.observe(config, db, job, journal, root, owned)
    else:
        maintenance_undo_files.check(
            root,
            packages.context(config, db, job["client_id"], job["vault_id"], write=True)[1],
            journal,
        )
    return original


def link_evidence(root: Any, allowed: Any, value: Any) -> list[Any]:
    """Locate legacy unresolved links only in source revisions proven by its old plan."""
    snapshots = {
        row[0]: {"sha256": row[1], "file_key": row[4]} for row in value.get("note_snapshot", [])
    }
    snapshots.update(value["source_files"])
    texts: dict[str, str] = {}
    result = []
    for issue in value.get("unresolved_links", []):
        path = issue.get("note_path")
        if not isinstance(path, str) or not allowed.allows(path) or path not in snapshots:
            raise IntegrityError("maintenance_archive_binding_changed")
        if path not in texts:
            raw, key = publication_file_io.read(root, path)
            if digest(raw) != snapshots[path]["sha256"] or key != snapshots[path]["file_key"]:
                raise IntegrityError("maintenance_note_changed")
            texts[path] = raw.decode("utf-8-sig")
        text = texts[path]
        try:
            offsets = [
                begin
                for begin, end, wiki, _, _ in publication_links.references(text)
                if (
                    text[begin:end].split("|", 1)[0].split("#", 1)[0]
                    if wiki
                    else text[begin:end].split("#", 1)[0]
                )
                == issue["target"]
            ]
        except publication_links.LinkReviewError as error:
            offsets = [error.offset] if issue["target"] == "unsupported_link_syntax" else []
        if not offsets:
            result.append(issue)
        for offset in offsets:
            result.append(
                {
                    **issue,
                    "line": text.count("\n", 0, offset) + 1,
                    "column": offset - text.rfind("\n", 0, offset),
                }
            )
    return result


def task_binding(job: Any) -> Any:
    """Index observations and timing never alter a confirmed file-action contract."""
    presentation = {
        "updated_at",
        "index_state",
        "index_error",
        "index_request_id",
        "index_requested_at",
        "index_observation",
        "index_dependency",
        "last_progress_at",
        "activity",
        "timings",
        "read_work",
    }
    return {key: value for key, value in job.items() if key not in presentation}
