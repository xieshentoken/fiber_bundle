"""Explicit metadata-only reconciliation of wholly missing published note bodies."""

import json
import sqlite3
import time
from typing import Any
from uuid import uuid4

from . import auth, database, note_placement, publication_locations, vaults
from .config import Config, IntegrityError, InvalidInput, contained
from .inputs import canonical
from .objects import digest


def observe(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vault: str,
    kind: str,
    identity: str,
    revision: int,
) -> dict[str, Any]:
    for permission in ("query", "add"):
        auth.grant(db, client, vault, permission)
    location = publication_locations.get(db, kind, identity)
    if (
        not location
        or location["vault_id"] != vault
        or not publication_locations.committed(db, location)
    ):
        raise InvalidInput("layout_missing_published_source_required")
    if location["revision"] != revision:
        raise IntegrityError("layout_rebind_revision_changed")
    root = vaults.checked_vault(config, auth.grant(db, client, vault, "query"))
    plan = location["plan"]
    body_paths = {
        path
        for logical, path in plan["mapping"].items()
        if logical.endswith(".md") and logical in plan.get("body_files", list(plan["mapping"]))
    }
    if not body_paths or location["entry_note_path"] not in body_paths:
        raise InvalidInput("layout_missing_body_required")
    for path in body_paths:
        vaults.require_scope(config, auth.grant(db, client, vault, "add"), path)
    # Any remaining body or symlink requires real location repair, never concealment.
    if any(contained(root, p).exists() or contained(root, p).is_symlink() for p in body_paths):
        raise IntegrityError("layout_missing_body_still_present")
    return {
        "client_id": client,
        "vault_id": vault,
        "kind": kind,
        "publication_id": identity,
        "revision": revision,
        "entry_note_path": location["entry_note_path"],
        "missing_paths": sorted(body_paths),
        "root_key": [root.stat().st_dev, root.stat().st_ino],
        "location_sha256": digest(canonical(plan).encode()),
    }


def preview(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vault: str,
    kind: str,
    identity: str,
    revision: int,
) -> dict[str, Any]:
    note_placement.guard(db, vault)
    value = observe(config, db, client, vault, kind, identity, revision)
    value["expires_at"] = time.time() + 600
    operation = str(uuid4())
    db.execute(
        "INSERT INTO vault_operations VALUES(?,?,?,'prepared',?,?,?)",
        (operation, vault, "publication_missing", canonical(value), time.time(), time.time()),
    )
    return {
        "operation_id": operation,
        "state": "prepared",
        "missing_paths": value["missing_paths"],
        "history_preserved": True,
        "vault_write_performed": False,
    }


def apply(
    config: Config, db: sqlite3.Connection, client: str, vault: str, operation: str
) -> dict[str, Any]:
    row = db.execute(
        "SELECT * FROM vault_operations WHERE id=? AND vault_id=? AND kind='publication_missing'",
        (operation, vault),
    ).fetchone()
    if not row:
        raise auth.Denied(404)
    value = json.loads(row["payload"])
    if value["client_id"] != client:
        raise auth.Denied(404)
    for permission in ("query", "add"):
        auth.grant(db, client, vault, permission)
    if row["state"] == "completed":
        return {"state": "missing", "history_preserved": True, "vault_write_performed": False}
    if row["state"] != "prepared" or time.time() > value["expires_at"]:
        raise IntegrityError("placement_preview_expired")
    current = observe(
        config, db, client, vault, value["kind"], value["publication_id"], value["revision"]
    )
    if any(canonical(v) != canonical(value[k]) for k, v in current.items()):
        raise IntegrityError("layout_rebind_preview_changed")
    with database.transaction(db):
        location = publication_locations.get(db, value["kind"], value["publication_id"])
        assert location is not None
        plan = location["plan"]
        plan["availability"] = {
            "state": "missing",
            "operation_id": operation,
            "missing_paths": value["missing_paths"],
        }
        changed = db.execute(
            "UPDATE publication_locations SET state='reconcile',value=?,revision=revision+1 "
            "WHERE kind=? AND publication_id=? AND revision=? AND state='published'",
            (canonical(plan), value["kind"], value["publication_id"], value["revision"]),
        ).rowcount
        if changed != 1:
            raise IntegrityError("layout_rebind_revision_changed")
        db.execute(
            "UPDATE vault_operations SET state='completed',updated_at=? WHERE id=?",
            (time.time(), operation),
        )
    from .note_search_jobs import record

    record(config, vault, "pending", reason="document_publication")
    return {"state": "missing", "history_preserved": True, "vault_write_performed": False}
