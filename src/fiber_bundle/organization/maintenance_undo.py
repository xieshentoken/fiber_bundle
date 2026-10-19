"""Task-owned compensation journal. No whole-vault restore and no model side effects."""

import json
from typing import Any

from .. import auth, moc_file_journal, moc_files, note_inventory, note_transfer_links
from ..config import IntegrityError
from . import maintenance_undo_files as files
from . import maintenance_undo_state as state
from . import packages, store


def save(config: Any, job: Any, value: Any) -> None:
    store.put(config, "maintenance_undo", job["id"], job["vault_id"], value)


def plans(config: Any, job: Any) -> list[Any]:
    return [
        p for m in job["mocs"] for p in moc_file_journal.plans(config, job["vault_id"], m["id"])
    ]


def note_stages(config: Any) -> set[str]:
    home = config.state_root / "moc-note-files"
    return (
        {p.name for p in home.iterdir() if p.is_dir() and not p.is_symlink()}
        if home.exists()
        else set()
    )


def capture(config: Any, db: Any, job: Any, paths: Any) -> None:
    journal = store.get(config, "maintenance_undo", job["id"])
    root, scope = packages.context(config, db, job["client_id"], job["vault_id"], write=True)
    for path in paths:
        if not scope.allows(path):
            raise IntegrityError("maintenance_undo_scope_changed")
        if path not in journal["files"]:
            evidence = files.observe(root, path)
            journal["files"][path] = {"before": evidence, "after": evidence}
    save(config, job, journal)


def begin(config: Any, db: Any, job: Any, item: Any) -> None:
    for other in store.rows(config, "jobs", job["vault_id"]):
        if (
            other["id"] != job["id"]
            and other.get("maintenance")
            and other.get("confirmed")
            and other["state"] not in {"completed", "cancelled"}
            and store.get(config, "maintenance_undo", other["id"])
        ):
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
    journal.update(
        state="applying",
        unit_plans=[p.plan_id for p in plans(config, job)],
        unit_stages=sorted(note_stages(config)),
        unit_operations=[
            r[0]
            for r in db.execute(
                "SELECT id FROM vault_operations WHERE vault_id=?", (job["vault_id"],)
            )
        ],
    )
    save(config, job, journal)
    paths = [m["path"] for m in job["mocs"]]
    if item and item["kind"] == "note":
        paths.append(item["path"])
    capture(config, db, job, paths)


def archive(config: Any, db: Any, job: Any, value: Any) -> None:
    """Called after the archive plan exists, before any of its file operations."""
    journal = store.get(config, "maintenance_undo", job["id"])
    if journal is None:
        raise IntegrityError("maintenance_undo_journal_required")
    paths = [u["path"] for u in value["updates"]]
    paths += [p["path"] for p in value.get("moc_file_projections", [])]
    capture(config, db, job, paths)
    journal = store.get(config, "maintenance_undo", job["id"])
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
        if p.plan_id not in journal["unit_plans"] or p.plan_id in recorded
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
    candidates = [p.path / "candidate.md" for p in owned]
    candidates += [
        config.state_root / "moc-note-files" / n / "candidate.md"
        for n in note_stages(config) - set(journal["unit_stages"])
    ]
    expected = {path: v["after"] for path, v in journal["files"].items()}
    for p in owned:
        if p.plan_id not in {p["id"] for p in journal["plans"]}:
            journal["plans"].append({"id": p.plan_id, "moc": p.value["moc_id"]})
    for moc in job["mocs"]:
        latest = moc_file_journal.latest(moc_file_journal.plans(config, job["vault_id"], moc["id"]))
        if latest and latest.plan_id in {p.plan_id for p in owned}:
            actual = files.observe(root, latest.value["path"])
            if actual and actual["sha256"] == latest.value["after_sha256"]:
                expected[latest.value["path"]] = actual
    for item in job["items"]:
        record = store.get(config, "note_moc", job["vault_id"] + ":" + item["entity"])
        if record and record["path"] in expected:
            expected[record["path"]] = {
                "sha256": record["after_sha256"],
                "file_key": record["after_file_key"],
            }
    for moc in job["new_mocs"]:
        if moc.get("claim") and expected.get(moc["path"]) is None:
            actual = files.observe(root, moc["path"])
            if actual and actual["sha256"] == moc["sha256"]:
                expected[moc["path"]] = actual
    operations = [
        dict(r)
        for r in db.execute(
            "SELECT id,payload FROM vault_operations WHERE vault_id=?", (job["vault_id"],)
        )
        if r["id"] not in journal["unit_operations"] or r["id"] in journal["operations"]
    ]
    for operation in operations:
        value = json.loads(operation["payload"])
        if operation["id"] not in journal["operations"]:
            journal["operations"].append(operation["id"])
        for exchange in value.get("exchanges", {}).values():
            candidates.append(root / exchange["stage"])
        if "moves" in value and value.get("source_root"):
            for update in value["updates"]:
                path = (
                    value["moves"].get(update["path"], update["path"])
                    if value.get("move_applied")
                    else update["path"]
                )
                actual = files.observe(root, path)
                if actual and actual["sha256"] == update["after_sha256"]:
                    expected[path] = actual
            if value.get("move_applied"):
                move = next(
                    (m for m in journal["moves"] if m["source"] == value["source_root"]), None
                )
                if move:
                    move["files"] = {}
                    for p, new in value["moves"].items():
                        actual = files.observe(root, new)
                        update = next((u for u in value["updates"] if u["path"] == p), None)
                        wanted = (
                            update["after_sha256"] if update else value["source_files"][p]["sha256"]
                        )
                        if not actual or actual["sha256"] != wanted:
                            raise IntegrityError("maintenance_undo_move_conflict")
                        move["files"][p] = actual
                for old, new in value["moves"].items():
                    if old in expected:
                        expected[new] = expected.pop(old)
                        expected[old] = None
    backups = {}
    for path in candidates:
        evidence = files.observe(path.parent, path.name)
        if evidence:
            backups[json.dumps(evidence, sort_keys=True)] = str(path)
    for path, evidence in journal["files"].items():
        actual = files.observe(root, path)
        wanted = expected.get(path)
        if actual != wanted:
            raise IntegrityError("maintenance_undo_file_conflict")
        evidence["after"] = actual
        if evidence["before"] and evidence["before"] != actual and not evidence.get("backup"):
            evidence["backup"] = backups.get(json.dumps(evidence["before"], sort_keys=True))
    journal.update(
        after_state=state.snapshot(
            config,
            db,
            job["vault_id"],
            persist=True,
            compact=journal.get("snapshot_version") == 2,
        ),
        state="ready",
    )
    save(config, job, journal)


def cancel(config: Any, db: Any, job: Any) -> None:
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
