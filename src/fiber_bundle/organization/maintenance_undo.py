"""Task-owned compensation journal. No whole-vault restore and no model side effects."""

from typing import Any

from .. import auth, moc_file_journal, moc_files, note_inventory, note_transfer_links
from ..config import IntegrityError
from . import maintenance_undo_files as files
from . import maintenance_undo_ownership as ownership
from . import maintenance_undo_seal as seal_evidence
from . import maintenance_undo_state as state
from . import packages, store
from .maintenance_undo_seal import note_stages as note_stages
from .maintenance_undo_seal import operation_rows as operation_rows


def save(config: Any, job: Any, value: Any) -> None:
    store.put(config, "maintenance_undo", job["id"], job["vault_id"], value)


def plans(config: Any, job: Any) -> list[Any]:
    return [
        p for m in job["mocs"] for p in moc_file_journal.plans(config, job["vault_id"], m["id"])
    ]


def capture(config: Any, db: Any, job: Any, paths: Any) -> None:
    journal = store.get(config, "maintenance_undo", job["id"])
    root, scope = packages.context(config, db, job["client_id"], job["vault_id"], write=True)
    if any(not scope.allows(path) for path in paths):
        raise IntegrityError("maintenance_undo_scope_changed")
    needed = dict.fromkeys(path for path in paths if path not in journal["files"])
    for path, evidence in files.observe_many(root, needed).items():
        journal["files"][path] = {"before": evidence, "after": evidence}
    save(config, job, journal)


def begin(config: Any, db: Any, job: Any, item: Any) -> None:
    from .maintenance_admission import blocker

    if blocker(config, job):
        raise IntegrityError("maintenance_other_task_unfinished")
    journal = store.get(config, "maintenance_undo", job["id"])
    if journal and journal["state"] == "applying":
        seal(config, db, job)
        journal = store.get(config, "maintenance_undo", job["id"])
    if journal is None:
        baseline = state.snapshot(config, db, job["vault_id"], persist=True, compact=True)
        journal = {
            "id": job["id"],
            "snapshot_version": 2,
            "state": "ready",
            "files": {},
            "moves": [],
            "plans": [],
            "operations": [],
            "before_state": baseline,
            "after_state": baseline,
            "legacy_applied": any(
                i.get("steps") or i.get("archive_operation") or i.get("intent")
                for i in job["items"]
            )
            or any(m.get("adopted") or m.get("claim") for m in job["new_mocs"]),
        }
    else:
        state.refresh_unowned(
            journal["before_state"],
            journal["after_state"],
            state.snapshot(
                config,
                db,
                job["vault_id"],
                persist=True,
                compact=journal.get("snapshot_version") == 2,
            ),
        )
    touched = set(item["add"] + item["remove"]) if item else set()
    paths = [m["path"] for m in job["mocs"] if m["id"] in touched or m.get("new")]
    if item and item["kind"] == "note" and item.get("phase") != "archived":
        paths.append(item["path"])
    root, scope = packages.context(config, db, job["client_id"], job["vault_id"], write=True)
    if any(not scope.allows(path) for path in paths):
        raise IntegrityError("maintenance_undo_scope_changed")
    needed = dict.fromkeys(path for path in paths if path not in journal["files"])
    # Snapshot reads precede the applying marker. An unavailable source has not
    # started a file transaction and can wait without stranding a write journal.
    evidence = files.observe_many(root, needed)
    for path, observed in evidence.items():
        journal["files"][path] = {"before": observed, "after": observed}
    journal.update(
        state="applying",
        unit_paths=paths,
        unit_owned_operations=[item["archive_operation"]]
        if item and item.get("archive_operation")
        else [],
        unit_plans=[p.plan_id for p in plans(config, job)],
        unit_stages=sorted(seal_evidence.note_stages(config)),
        unit_operations=[
            r[0]
            for r in db.execute(
                "SELECT id FROM vault_operations WHERE vault_id=?", (job["vault_id"],)
            )
        ],
    )
    save(config, job, journal)


def archive(config: Any, db: Any, job: Any, value: Any) -> None:
    """Called after the archive plan exists, before any of its file operations."""
    journal = store.get(config, "maintenance_undo", job["id"])
    if journal is None:
        raise IntegrityError("maintenance_undo_journal_required")
    paths = [u["path"] for u in value["updates"]]
    paths += [p["path"] for p in value.get("moc_file_projections", [])]
    capture(config, db, job, paths)
    journal = store.get(config, "maintenance_undo", job["id"])
    if "unit_paths" in journal:
        journal["unit_paths"] = sorted(
            set(journal["unit_paths"]) | set(paths) | set(value["moves"])
        )
    if not any(m["source"] == value["source_root"] for m in journal["moves"]):
        journal["moves"].append(
            {
                "source": value["source_root"],
                "target": value["target_root"],
                "package": value.get("package", value.get("kind") == "expert"),
                "files": {},
            }
        )
    save(config, job, journal)


def seal(config: Any, db: Any, job: Any) -> None:
    journal = store.get(config, "maintenance_undo", job["id"])
    if not journal or journal["state"] != "applying":
        return
    root, scope = packages.context(config, db, job["client_id"], job["vault_id"], write=True)
    recorded = {p["id"] for p in journal["plans"]}
    owned = [
        p
        for p in plans(config, job)
        if p.value["path"] in journal["files"]
        and (
            p.plan_id not in journal["unit_plans"]
            or ("unit_paths" not in journal and p.plan_id in recorded)
        )
    ]
    # A crash after an exclusive Map exchange can precede its outcome marker.
    # Verify both inodes through the existing reconciler before sealing undo;
    # otherwise our own new Map is mistaken for an external file edit.
    for plan in owned:
        if plan.status == "pending":
            moc_files.apply_file(
                config,
                db,
                job["client_id"],
                job["vault_id"],
                plan.value["moc_id"],
                plan.plan_id,
                reconcile=True,
            )
    seal_evidence.observe(config, db, job, journal, root, owned)
    ownership.seal(
        config,
        job,
        journal,
        state.snapshot(
            config,
            db,
            job["vault_id"],
            persist=True,
            compact=journal.get("snapshot_version") == 2,
        ),
    )
    journal["state"] = "ready"
    save(config, job, journal)


def cancel(config: Any, db: Any, job: Any) -> None:
    import json

    for row in db.execute(
        "SELECT payload FROM vault_operations WHERE vault_id=? AND "
        "kind='maintenance_link_repair' AND state IN ('applying','reconcile','completed')",
        (job["vault_id"],),
    ):
        if json.loads(row[0])["job"] == job["id"]:
            raise IntegrityError("maintenance_undo_link_repair_completed")
    journal = store.get(config, "maintenance_undo", job["id"])
    if journal and journal.get("legacy_applied"):
        raise IntegrityError("maintenance_undo_legacy_journal_missing")
    if journal is None:
        if job.get("confirmed") and any(
            i.get("steps") or i.get("archive_operation") for i in job["items"]
        ):
            raise IntegrityError("maintenance_undo_legacy_journal_missing")
        job.update(state="cancelled", error=None, generation=job.get("generation", 0) + 1)
    else:
        job.update(
            state="cancelling",
            stage="rollback",
            error=None,
            generation=job.get("generation", 0) + 1,
            rollback_progress=0,
            rollback_total=len(journal["files"]),
        )
    store.put(config, "jobs", job["id"], job["vault_id"], job)


def run(config: Any, db: Any, job: Any) -> Any:
    journal = store.get(config, "maintenance_undo", job["id"])
    root, scope = packages.context(config, db, job["client_id"], job["vault_id"], write=True)
    try:
        info = root.stat()
        if [info.st_dev, info.st_ino] != job["fixed"]["root_key"] or scope.wire() != job["fixed"][
            "scope"
        ]:
            raise IntegrityError("maintenance_undo_scope_changed")
        if journal["state"] == "applying":
            seal(config, db, job)
            journal = store.get(config, "maintenance_undo", job["id"])

        def persist() -> None:
            save(config, job, journal)

        if journal["state"] == "ready":
            # Historical journals may already have captured unrelated writes.
            # Prune only their unsupported ownership; retain their original
            # after-images so normal CAS checks still detect subsequent edits.
            ownership.seal(config, job, journal, journal["after_state"])
            journal["operations"] = sorted(ownership.operations(db, job, journal))
            persist()
            state.preflight(config, journal["before_state"], journal["after_state"])
            files.check(root, scope, journal)
            if job["new_mocs"]:
                inventory = note_inventory.read(
                    config, db, auth.grant(db, job["client_id"], job["vault_id"], "query")
                )
                owned_paths = {
                    p
                    for p, evidence in journal["files"].items()
                    if evidence["before"] != evidence["after"]
                } | {m["target"] for m in journal["moves"] if m["files"]}
                for moc in job["new_mocs"]:
                    if any(
                        ref["note_path"] not in owned_paths
                        for ref in note_transfer_links.inbound(inventory, moc["path"])
                    ):
                        raise IntegrityError("maintenance_undo_relationship_conflict")
            state.check(
                journal["before_state"],
                journal["after_state"],
                state.snapshot(
                    config, db, job["vault_id"], compact=journal.get("snapshot_version") == 2
                ),
            )
            journal["state"] = "restoring"
            persist()
        if not files.restore(config, root, scope, journal, persist):
            job["rollback_progress"] = sum(
                v.get("restored", False) for v in journal["files"].values()
            )
            store.put(config, "jobs", job["id"], job["vault_id"], job)
            return job
        state.restore(config, db, job, journal, persist)
        for own in journal["plans"]:
            p = next(
                p
                for p in moc_file_journal.plans(config, job["vault_id"], own["moc"])
                if p.plan_id == own["id"]
            )
            if p.status in {"applied", "pending", "planned"}:
                marker = p.path / "reverted"
                if not marker.exists():
                    moc_file_journal.write(marker, b"reverted\n")
        for identity in journal["operations"]:
            db.execute("UPDATE vault_operations SET state='cancelled' WHERE id=?", (identity,))
        journal["state"] = "reverted"
        persist()
        job.update(
            state="cancelled",
            error=None,
            index_state="pending",
            rollback_completed=True,
            rollback_progress=len(journal["files"]),
        )
    except Exception as error:
        job.update(state="rollback_conflict", error=str(error)[:180])
    store.put(config, "jobs", job["id"], job["vault_id"], job)
    return job
