"""Converge task-owned archive journals before validating another item sharing its Maps."""

import json
from typing import Any

from .. import moc_file_journal, placement_committed
from ..config import IntegrityError
from . import maintenance_archive, packages, store


def owned_operations(db: Any, job: Any) -> list[Any]:
    owned = []
    for item in job["items"]:
        operation = item.get("archive_operation")
        if not operation or item.get("archive_deferral"):
            continue
        row = db.execute(
            "SELECT * FROM vault_operations WHERE id=? AND vault_id=?",
            (operation, job["vault_id"]),
        ).fetchone()
        if row is None:
            raise IntegrityError("maintenance_archive_journal_missing")
        value = json.loads(row["payload"])
        expected_kind = (
            "note_placement" if item.get("archive_kind") == "managed" else "maintenance_archive"
        )
        if (
            row["kind"] != expected_kind
            or value.get("client_id") != job["client_id"]
            or value.get("vault_id") != job["vault_id"]
            or value.get("source_root") != item["path"]
            or value.get("target_root") != item.get("target")
            or expected_kind == "maintenance_archive"
            and value.get("fixed") != job["fixed"]
        ):
            raise IntegrityError("maintenance_archive_binding_changed")
        owned.append((row, value, item))
    return sorted(owned, key=lambda entry: (entry[0]["created_at"], entry[0]["id"]))


def _advance(config: Any, db: Any, job: Any, operations: list[Any]) -> dict[str, Any]:
    from .maintenance_apply import observe

    changes = {}
    for mid, expected in job.get("moc_expected", {}).items():
        specs = [
            (row["id"], spec)
            for row, value, _ in operations
            if row["state"] == "completed" and value.get("metadata_committed")
            for spec in value.get("moc_file_projections", [])
            if spec["moc_id"] == mid
        ]
        if not specs:
            continue
        current = observe(config, db, job, mid)
        if current == expected:
            continue
        if current["revision"] != expected["revision"] or current["members"] != expected["members"]:
            raise IntegrityError("maintenance_membership_baseline_changed")
        plans = moc_file_journal.plans(config, job["vault_id"], mid)
        latest = moc_file_journal.latest(plans)
        sha = expected["sha256"]
        consumed: set[str] = set()
        final = None
        # Follow exact immutable before/after edges, independently for each Map.
        # Item order is not a proof and cannot choose between competing histories.
        while sha != current["sha256"]:
            candidates = [
                (operation, spec)
                for operation, spec in specs
                if operation not in consumed
                and spec["before_sha256"] == sha
                and spec["revision"] == current["revision"]
            ]
            if len(candidates) != 1:
                raise IntegrityError("maintenance_membership_baseline_changed")
            operation, spec = candidates[0]
            matches = [
                plan
                for plan in plans
                if plan.status == "applied"
                and plan.value["operation"] == operation
                and all(
                    plan.value[k] == spec[k]
                    for k in (
                        "path",
                        "revision",
                        "base_id",
                        "before_sha256",
                        "after_sha256",
                        "managed_sha256",
                    )
                )
            ]
            if len(matches) != 1:
                raise IntegrityError("maintenance_membership_baseline_changed")
            final = matches[0]
            consumed.add(operation)
            sha = spec["after_sha256"]
        if not final or not latest or latest.plan_id != final.plan_id:
            raise IntegrityError("maintenance_membership_baseline_changed")
        # The final plan binds the actual exchanged inode, not only equivalent text.
        from ..publication_file_io import read

        root, allowed = packages.context(config, db, job["client_id"], job["vault_id"], write=True)
        if not allowed.allows(final.value["path"]):
            raise IntegrityError("maintenance_archive_scope_changed")
        _, key = read(root, final.value["path"])
        if final.value.get("after_file_key") and key != final.value["after_file_key"]:
            raise IntegrityError("maintenance_membership_baseline_changed")
        changes[mid] = current
    return changes


def resume(
    config: Any,
    db: Any,
    job: Any,
    *,
    operation_ids: set[str] | None = None,
    namespace_addition: Any = None,
) -> None:
    """Finish only committed writes; unfinished moves keep their existing execution path."""
    operations = [
        entry
        for entry in owned_operations(db, job)
        if operation_ids is None or entry[0]["id"] in operation_ids
    ]
    for row, value, item in operations:
        if value.get("metadata_committed") and row["state"] != "completed":
            if row["state"] not in {"applying", "reconcile"}:
                raise IntegrityError("placement_committed_evidence_missing")
            maintenance_archive.apply(
                config,
                db,
                job,
                item,
                **(
                    {"namespace_addition": namespace_addition}
                    if namespace_addition is not None
                    else {}
                ),
            )
    operations = [
        entry
        for entry in owned_operations(db, job)
        if operation_ids is None or entry[0]["id"] in operation_ids
    ]
    changes = _advance(config, db, job, operations)
    if not changes:
        return
    completed = [entry for entry in operations if entry[0]["state"] == "completed"]
    if not completed:
        raise IntegrityError("maintenance_membership_baseline_changed")
    row, value, _ = completed[-1]
    root, allowed = packages.context(config, db, job["client_id"], job["vault_id"], write=True)
    if row["kind"] == "note_placement":
        from ..note_placement_execution import check

        check(config, db, value)
    placement_committed.verify(
        config,
        db,
        value,
        root,
        allowed,
        **({"namespace_addition": namespace_addition} if namespace_addition is not None else {}),
    )
    job["moc_expected"].update(changes)
    for row, value, item in operations:
        if row["state"] == "completed" and value.get("metadata_committed"):
            item["phase"] = "archived"
    store.put(config, "jobs", job["id"], job["vault_id"], job)
