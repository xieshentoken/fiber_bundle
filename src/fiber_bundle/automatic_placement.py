"""Committed MOC associations queue bounded same-Vault placement, independently of ingestion."""

import json
import sqlite3
import time
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from . import auth, database, note_inventory, note_placement, vault_directories
from .attachment_projection import epoch
from .config import Config, IntegrityError, InvalidInput, StorageError
from .inputs import canonical
from .objects import digest

KIND = "automatic_placement"


def persist(
    db: sqlite3.Connection,
    identity: str,
    value: dict[str, Any],
    state: str,
    reason: str | None = None,
) -> None:
    value["reason"] = reason
    db.execute(
        "UPDATE vault_operations SET state=?,payload=?,updated_at=? WHERE id=?",
        (state, canonical(value), time.time(), identity),
    )


def freeze(
    config: Config, db: sqlite3.Connection, client: str, vault: str, identity: str
) -> dict[str, Any]:
    root, _, policy = note_placement.context(config, db, client, vault)
    if policy["settings"].get("moc_archive_mode") != "automatic":
        raise InvalidInput("automatic_placement_disabled")
    inventory = note_inventory.read(config, db, auth.grant(db, client, vault, "query"))
    note = next((n for n in inventory.notes if n.identity == identity), None)
    if note is None:
        raise InvalidInput("automatic_placement_note_unavailable")
    location = next(
        (
            r
            for r in note_placement.locations(db, vault)
            if note.relative_path in r["plan"]["outputs"]
        ),
        None,
    )
    if location is None:
        raise InvalidInput("placement_managed_note_required")
    from .publication_locations import note_id

    ids = {
        location["plan"]
        .get("identity_overrides", {})
        .get(logical, note_id(location["kind"], location["publication_id"], logical))
        for logical in location["plan"]["mapping"]
        if logical.endswith(".md")
    }
    entry_logical = next(
        (
            logical
            for logical, path in location["plan"]["mapping"].items()
            if path == location["entry_note_path"]
        ),
        None,
    )
    if entry_logical:
        identity = (
            location["plan"]
            .get("identity_overrides", {})
            .get(
                entry_logical, note_id(location["kind"], location["publication_id"], entry_logical)
            )
        )
        note = next((n for n in inventory.notes if n.identity == identity), note)
    associations = {
        mid: {"revision": record.revision, "members": record.members}
        for mid, record in note_placement.records(config, vault).items()
        if record and ids.intersection(record.members)
    }
    if not associations:
        raise InvalidInput("automatic_placement_no_committed_moc")
    return {
        "client_id": client,
        "vault_id": vault,
        "note_identity": identity,
        "note_sha256": note.sha256,
        "file_key": list(note.file_key or ()),
        "note_path": note.relative_path,
        "root_key": vault_directories.key(root),
        "permission_epoch": epoch(db, client, vault),
        "policy_revision": policy["revision"],
        "associations": associations,
        "kind": location["kind"],
        "publication_id": location["publication_id"],
    }


def enqueue(
    config: Config, db: sqlite3.Connection, client: str, vault: str, identity: str
) -> dict[str, Any]:
    binding = freeze(config, db, client, vault, identity)
    operation = str(uuid5(NAMESPACE_URL, KIND + ":" + digest(canonical(binding).encode())))
    now = time.time()
    value = {"binding": binding, "operation_id": None, "reason": None}
    # A later association supersedes only work which has not started side effects.
    db.execute(
        "UPDATE vault_operations SET state='superseded',updated_at=? WHERE kind=? "
        "AND vault_id=? AND state='queued' AND id!=? "
        "AND json_extract(payload,'$.binding.publication_id')=?",
        (now, KIND, vault, operation, binding["publication_id"]),
    )
    db.execute(
        "INSERT OR IGNORE INTO vault_operations VALUES(?,?,?,'queued',?,?,?)",
        (operation, vault, KIND, canonical(value), now, now),
    )
    row = db.execute("SELECT state FROM vault_operations WHERE id=?", (operation,)).fetchone()
    return {"operation_id": operation, "state": row[0], "note_identity": identity}


def listing(db: sqlite3.Connection, client: str, vault: str) -> list[dict[str, Any]]:
    return [
        {
            "id": row["id"],
            "state": row["state"],
            "note_identity": value["binding"]["note_identity"],
            "note_path": value["binding"]["note_path"],
            "reason": value.get("reason"),
            "placement_operation_id": value.get("operation_id"),
        }
        for row in db.execute(
            "SELECT * FROM vault_operations WHERE kind=? AND vault_id=? "
            "ORDER BY updated_at DESC LIMIT 100",
            (KIND, vault),
        )
        for value in [json.loads(row["payload"])]
        if value["binding"]["client_id"] == client
    ]


def reconcile(
    config: Config, db: sqlite3.Connection, client: str, vault: str, identity: str, action: str
) -> dict[str, str]:
    row = db.execute(
        "SELECT * FROM vault_operations WHERE kind=? AND vault_id=? AND id=?",
        (KIND, vault, identity),
    ).fetchone()
    if row is None:
        raise auth.Denied(404)
    value = json.loads(row["payload"])
    if value["binding"]["client_id"] != client:
        raise auth.Denied(404)
    auth.grant(db, client, vault, "add")
    if row["state"] not in {"queued", "needs_review", "needs_selection", "reconcile", "index"}:
        raise InvalidInput("automatic_placement_not_pending")
    if action == "cancel":
        if value["operation_id"]:
            op = db.execute(
                "SELECT state FROM vault_operations WHERE id=?", (value["operation_id"],)
            ).fetchone()
            if op[0] not in {"prepared", "cancelled"}:
                raise IntegrityError("placement_reconciliation_required")
            db.execute(
                "UPDATE vault_operations SET state='cancelled' WHERE id=?", (value["operation_id"],)
            )
        persist(db, identity, value, "cancelled")
        return {"state": "cancelled"}
    # Never replace the frozen binding to silently adopt external edits or grant changes.
    if not value["operation_id"]:
        current = freeze(config, db, client, vault, value["binding"]["note_identity"])
        if current != value["binding"]:
            raise IntegrityError("automatic_placement_binding_changed")
        persist(db, identity, value, "queued")
    else:
        root, _, policy = note_placement.context(config, db, client, vault)
        binding = value["binding"]
        if (
            policy["revision"] != binding["policy_revision"]
            or epoch(db, client, vault) != binding["permission_epoch"]
            or vault_directories.key(root) != binding["root_key"]
        ):
            raise IntegrityError("automatic_placement_binding_changed")
        from .note_placement_execution import apply

        apply(config, db, client, vault, value["operation_id"], reconcile=True)
        persist(db, identity, value, "index")
    return {"state": "queued" if not value["operation_id"] else "index"}


def tick(config: Config) -> bool:
    with database.connect(config.state_root) as peek:
        if not peek.execute(
            "SELECT 1 FROM vault_operations WHERE kind=? AND state IN ('queued','index') LIMIT 1",
            (KIND,),
        ).fetchone():
            return False
    with database.locked(config), database.connect(config.state_root) as db:
        from .vault_relocation.guard import operation

        row = next(
            (
                r
                for r in db.execute(
                    "SELECT * FROM vault_operations WHERE kind=? AND state IN ('queued','index') "
                    "ORDER BY updated_at,id",
                    (KIND,),
                )
                if operation(db, r["vault_id"]) is None
            ),
            None,
        )
        if row is None:
            return False
        value = json.loads(row["payload"])
        binding = value["binding"]
        client, vault = binding["client_id"], binding["vault_id"]
        identity = row["id"]
        try:
            if row["state"] == "queued":
                if freeze(config, db, client, vault, binding["note_identity"]) != binding:
                    raise IntegrityError("automatic_placement_binding_changed")
                if binding["kind"] == "expert":
                    persist(db, identity, value, "book_preserved")
                    return True
                from .note_placement_execution import apply

                persist(db, identity, value, "preparing")
                try:
                    preview = note_placement.preview(
                        config, db, client, vault, binding["note_identity"], binding["note_sha256"]
                    )
                except InvalidInput as exc:
                    if str(exc) != "placement_already_at_destination":
                        raise
                    persist(db, identity, value, "complete")
                    return True
                value["operation_id"] = preview["operation_id"]
                persist(db, identity, value, "applying")
                if not preview["ready"]:
                    persist(
                        db,
                        identity,
                        value,
                        "needs_selection" if not preview["target_path"] else "needs_review",
                        "placement_preview_not_ready",
                    )
                    return True
                apply(config, db, client, vault, preview["operation_id"])
                persist(db, identity, value, "index")
            else:
                # After a move, recheck authorization and policy instead of the old SHA.
                root, _, policy = note_placement.context(config, db, client, vault)
                if (
                    policy["revision"] != binding["policy_revision"]
                    or epoch(db, client, vault) != binding["permission_epoch"]
                    or vault_directories.key(root) != binding["root_key"]
                ):
                    raise IntegrityError("automatic_placement_binding_changed")
        except (StorageError, auth.Denied, OSError) as exc:
            persist(
                db,
                identity,
                value,
                "reconcile" if value["operation_id"] else "needs_review",
                str(exc)
                if isinstance(exc, StorageError)
                else "automatic_placement_authorization_changed",
            )
            return True
    try:
        from .note_search_build import build

        build(config, client, vault)
        with database.locked(config), database.connect(config.state_root) as db:
            root, _, policy = note_placement.context(config, db, client, vault)
            if (
                policy["revision"] != binding["policy_revision"]
                or epoch(db, client, vault) != binding["permission_epoch"]
                or vault_directories.key(root) != binding["root_key"]
            ):
                raise IntegrityError("automatic_placement_binding_changed_after_index")
            current = db.execute(
                "SELECT state FROM vault_operations WHERE id=?", (identity,)
            ).fetchone()
            if current and current[0] != "cancelled":
                persist(db, identity, value, "complete")
    except (StorageError, auth.Denied, OSError):
        with database.locked(config), database.connect(config.state_root) as db:
            persist(db, identity, value, "needs_review", "placement_index_requires_reconciliation")
    return True


def recover(config: Config) -> None:
    with database.locked(config), database.connect(config.state_root) as db:
        db.execute(
            "UPDATE vault_operations SET state='reconcile' WHERE kind=? "
            "AND state IN ('preparing','applying')",
            (KIND,),
        )
