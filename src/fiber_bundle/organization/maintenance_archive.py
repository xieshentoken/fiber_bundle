"""Archive plain notes/native Drafts using the existing exclusive move/CAS machinery."""

import json
import time
from dataclasses import asdict
from pathlib import PurePosixPath
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .. import (
    auth,
    moc_placement_projection,
    note_identity_store,
    note_inventory,
    note_placement,
    publication_file_io,
)
from .. import (
    note_placement_execution as execution,
)
from ..config import IntegrityError
from ..inputs import canonical
from ..objects import digest
from . import packages, store


def prepare(config: Any, db: Any, job: Any, item: Any) -> str:
    client, vault = job["client_id"], job["vault_id"]
    # Moving an existing plain note is authorized by the confirmed maintenance
    # snapshot, independently of permission to publish new material. Keep the
    # publication gate for managed bundles below; never enable/change a policy.
    note_placement.guard(db, vault)
    from ..moc_registration import guard as registration_guard

    registration_guard(db, vault)
    root, allowed = packages.context(config, db, client, vault, write=True)
    source, target = item["path"], item["target"]
    if not target or not allowed.allows(target) or (root / target).exists():
        raise IntegrityError("maintenance_archive_target_conflict")
    inventory = note_inventory.read(config, db, auth.grant(db, client, vault, "query"))
    if item["kind"] == "expert":
        package = store.get(config, "packages", item["entity"].removeprefix("expert:"))
        if package is None:
            raise IntegrityError("maintenance_external_package_required")
        content = publication_file_io.tree(root, source, allowed)
        if set(content) != set(package["files"]):
            raise IntegrityError("maintenance_package_members_changed")
        for name, data in content.items():
            if digest(data) != package["files"][name]["sha256"]:
                raise IntegrityError("maintenance_package_member_changed")
        paths = [source + "/" + name for name in content]
    else:
        paths = [source]
        raw, _ = publication_file_io.read(root, source)
        if digest(raw) != item["sha256"]:
            raise IntegrityError("maintenance_note_changed")
    moves = {path: target + path[len(source) :] for path in paths}
    updates, unresolved = note_placement.plan_links(
        root, allowed, inventory, moves, affected_only=True
    )
    if item["kind"] == "expert" and any(u["path"] in moves for u in updates):
        raise IntegrityError("maintenance_package_internal_rewrite_refused")
    if unresolved:
        raise IntegrityError("maintenance_archive_link_review_required")
    projections = moc_placement_projection.prepare(
        config, vault, inventory, note_placement.records(config, vault), None, moves, updates
    )
    operation = str(uuid5(NAMESPACE_URL, "maintenance-archive:" + job["id"] + ":" + item["entity"]))
    value = {
        "client_id": client,
        "vault_id": vault,
        "source_root": source,
        "target_root": target,
        "kind": "maintenance",
        "publication_id": item["entity"],
        "action": "archive",
        "moves": moves,
        "updates": updates,
        "moc_file_projections": projections,
        "source_files": {
            path: {
                "sha256": digest(publication_file_io.read(root, path)[0]),
                "file_key": publication_file_io.read(root, path)[1],
            }
            for path in paths
        },
        "registrations": [asdict(r) for r in note_identity_store.records(config, vault)],
        "move_applied": False,
        "exchanges": {},
        "package": item["kind"] == "expert",
        "fixed": job["fixed"],
        "moc_snapshot": {
            mid: asdict(r) for mid, r in note_placement.records(config, vault).items() if r
        },
    }
    db.execute(
        "INSERT INTO vault_operations VALUES(?,?,?,'prepared',?,?,?)",
        (operation, vault, "maintenance_archive", canonical(value), time.time(), time.time()),
    )
    return operation


def apply(config: Any, db: Any, job: Any, item: Any) -> None:
    operation = item.get("archive_operation")
    if not operation:
        locations = [
            r
            for r in note_placement.locations(db, job["vault_id"])
            if r and (r["entry_note_path"] == item["path"] or r["package_root"] == item["path"])
        ]
        flat_maintenance = (
            not job["fixed"]["save_policy"]["publication_ready"]
            and item["kind"] == "note"
            and all(
                r["state"] == "published"
                and not r["package_root"]
                and len(r["plan"].get("body_files", [])) <= 1
                for r in locations
            )
        )
        if locations and not flat_maintenance:
            location = locations[0]
            inventory = note_inventory.read(
                config, db, auth.grant(db, job["client_id"], job["vault_id"], "query")
            )
            entry = next(
                n for n in inventory.notes if n.relative_path == location["entry_note_path"]
            )
            if not entry.identity:
                raise IntegrityError("maintenance_managed_identity_unavailable")
            packet = note_placement.preview(
                config,
                db,
                job["client_id"],
                job["vault_id"],
                entry.identity,
                entry.sha256,
                action="expert_archive" if item["kind"] == "expert" else "archive",
                destination=str(PurePosixPath(item["target"]).parent),
                inventory=inventory,
            )
            item.update(archive_operation=packet["operation_id"], archive_kind="managed")
            store.put(config, "jobs", job["id"], job["vault_id"], job)
            operation = item["archive_operation"]
    if item.get("archive_kind") == "managed":
        row = db.execute("SELECT state FROM vault_operations WHERE id=?", (operation,)).fetchone()
        from .maintenance_undo import archive

        payload = db.execute(
            "SELECT payload FROM vault_operations WHERE id=?", (operation,)
        ).fetchone()[0]
        archive(config, db, job, json.loads(payload))
        execution.apply(
            config,
            db,
            job["client_id"],
            job["vault_id"],
            operation,
            reconcile=row["state"] not in {"prepared", "completed"},
        )
        item["phase"] = "archived"
        return
    if not operation:
        operation = prepare(config, db, job, item)
        item["archive_operation"] = operation
        store.put(config, "jobs", job["id"], job["vault_id"], job)
    row = db.execute(
        "SELECT * FROM vault_operations WHERE id=? AND kind='maintenance_archive'", (operation,)
    ).fetchone()
    if row is None:
        raise IntegrityError("maintenance_archive_journal_missing")
    value = json.loads(row["payload"])
    from .maintenance_undo import archive

    archive(config, db, job, value)
    root, allowed = packages.context(config, db, job["client_id"], job["vault_id"], write=True)
    if value["fixed"] != job["fixed"]:
        raise IntegrityError("maintenance_archive_binding_changed")
    if any(
        not allowed.allows(p)
        for p in set(value["moves"])
        | set(value["moves"].values())
        | {u["path"] for u in value["updates"]}
    ):
        raise IntegrityError("maintenance_archive_scope_changed")
    current = {
        mid: asdict(r) for mid, r in note_placement.records(config, job["vault_id"]).items() if r
    }
    if current != value["moc_snapshot"]:
        raise IntegrityError("maintenance_archive_membership_changed")
    if row["state"] != "completed":
        execution.persist(db, operation, value, "applying")
        try:
            if (
                not (root / value["source_root"]).exists()
                and (root / value["target_root"]).exists()
            ):
                execution.move(db, operation, value, root, allowed)
            for update in value["updates"]:
                execution.exchange(config, db, operation, value, root, allowed, update)
            execution.move(db, operation, value, root, allowed)
            execution.complete(config, db, operation, value, root)
        except BaseException:
            execution.persist(db, operation, value, "reconcile")
            raise
    moc_placement_projection.finalize(config, db, operation, value, root)
    if value["package"]:
        packages.register(config, db, job["client_id"], job["vault_id"], value["target_root"])
    item["phase"] = "archived"
