"""Explicit resumable batches composed of the existing single-publication repairs."""

import json
import sqlite3
import time
from typing import Any
from uuid import uuid4

from . import auth, database, note_identity_rebind, publication_availability, publication_rebind
from . import publication_reconcile_scan as discovery
from .config import Config, IntegrityError, InvalidInput, StorageError
from .inputs import canonical


def result(row: sqlite3.Row, value: dict[str, Any]) -> dict[str, Any]:
    return {
        "operation_id": row["id"],
        "state": row["state"],
        "items": value["items"],
        "history_preserved": True,
        "vault_write_performed": False,
        "index_update_required": row["state"] in {"partial", "completed"},
    }


def get(db: sqlite3.Connection, client: str, vault: str, operation: str) -> tuple[Any, Any]:
    for permission in ("query", "add"):
        auth.grant(db, client, vault, permission)
    row = db.execute(
        "SELECT * FROM vault_operations WHERE id=? AND vault_id=? "
        "AND kind='publication_reconcile_batch'",
        (operation, vault),
    ).fetchone()
    if not row:
        raise auth.Denied(404)
    value = json.loads(row["payload"])
    if value["client_id"] != client:
        raise auth.Denied(404)
    return row, value


def preview(
    config: Config, db: sqlite3.Connection, client: str, vault: str, choices: list[dict[str, Any]]
) -> dict[str, Any]:
    scanned = discovery.scan(config, db, client, vault)
    current = {(r["kind"], r["publication_id"]): r for r in scanned["items"]}
    seen = set()
    items = []
    ordinary = (
        note_identity_rebind.context(config, db, client, vault)
        if any(c["kind"] == "note" for c in choices)
        else None
    )
    with database.transaction(db):
        for choice in choices:
            key = choice["kind"], choice["publication_id"]
            if key in seen:
                raise InvalidInput("layout_reconcile_duplicate_selection")
            seen.add(key)
            item = current.get(key)
            if item is None or item["expected_revision"] != choice["expected_revision"]:
                raise IntegrityError("layout_rebind_preview_changed")
            args = (config, db, client, vault, *key, choice["expected_revision"])
            if choice["action"] == "missing":
                if not choice.get("deletion_confirmed") or not item["can_mark_missing"]:
                    raise InvalidInput("layout_reconcile_deletion_review_required")
                child = publication_availability.preview(*args)
                target = None
            else:
                target = choice.get("target_path")
                if not target:
                    raise InvalidInput("layout_rebind_invalid_entry")
                if choice["kind"] == "note":
                    assert ordinary is not None
                    child = note_identity_rebind.preview(
                        config,
                        db,
                        client,
                        vault,
                        "note:" + choice["publication_id"],
                        choice["expected_revision"],
                        target,
                        ordinary,
                    )
                else:
                    child = publication_rebind.preview(*args, target)
            child_row = db.execute(
                "SELECT payload FROM vault_operations WHERE id=?", (child["operation_id"],)
            ).fetchone()
            evidence = json.loads(child_row[0])
            # Child tickets are created only by explicit batch preview, never by scanning.
            items.append(
                {
                    "source_path": item["source_path"],
                    "target_path": target,
                    "action": choice["action"],
                    "kind": choice["kind"],
                    "operation_id": child["operation_id"],
                    "modified_notes": child.get("modified_notes", []),
                    "missing_paths": child.get("missing_paths", []),
                    "expires_at": evidence["expires_at"],
                    "state": "prepared",
                    "error": None,
                }
            )
        operation = str(uuid4())
        value = {"client_id": client, "vault_id": vault, "items": items}
        db.execute(
            "INSERT INTO vault_operations VALUES(?,?,?,'prepared',?,?,?)",
            (
                operation,
                vault,
                "publication_reconcile_batch",
                canonical(value),
                time.time(),
                time.time(),
            ),
        )
    row, value = get(db, client, vault, operation)
    return result(row, value)


def apply(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vault: str,
    operation: str,
    *,
    max_items: int = 1000,
) -> dict[str, Any]:
    row, value = get(db, client, vault, operation)
    if row["state"] == "completed":
        return result(row, value)
    missing_candidates = None
    ordinary = (
        note_identity_rebind.context(config, db, client, vault)
        if any(i.get("kind") == "note" and i["state"] != "completed" for i in value["items"])
        else None
    )
    db.execute(
        "UPDATE vault_operations SET state='applying',updated_at=? WHERE id=?",
        (time.time(), operation),
    )
    handled = 0
    for item in value["items"]:
        if item["state"] == "completed":
            continue
        if handled >= max_items:
            break
        handled += 1
        try:
            if item.get("kind") == "note":
                assert ordinary is not None
                note_identity_rebind.apply(
                    config, db, client, vault, item["operation_id"], ordinary
                )
            elif item["action"] == "missing":
                child = db.execute(
                    "SELECT state FROM vault_operations WHERE id=?", (item["operation_id"],)
                ).fetchone()
                if child[0] != "completed":
                    if missing_candidates is None:
                        missing_candidates = discovery.scan(config, db, client, vault)["items"]
                    child_value = json.loads(
                        db.execute(
                            "SELECT payload FROM vault_operations WHERE id=?",
                            (item["operation_id"],),
                        ).fetchone()[0]
                    )
                    issue = next(
                        (
                            i
                            for i in missing_candidates
                            if (i["kind"], i["publication_id"])
                            == (child_value["kind"], child_value["publication_id"])
                        ),
                        None,
                    )
                    if issue is None or not issue["can_mark_missing"]:
                        raise IntegrityError("layout_reconcile_deletion_review_required")
                publication_availability.apply(config, db, client, vault, item["operation_id"])
            else:
                child = db.execute(
                    "SELECT state FROM vault_operations WHERE id=?", (item["operation_id"],)
                ).fetchone()
                publication_rebind.apply(
                    config,
                    db,
                    client,
                    vault,
                    item["operation_id"],
                    reconcile=child[0] in {"applying", "reconcile"},
                )
            item.update(state="completed", error=None)
        except (StorageError, OSError, sqlite3.Error) as exc:
            code = str(exc)
            if (
                not code.startswith(("layout_", "placement_"))
                or not code.replace("_", "").isalnum()
            ):
                code = "layout_reconcile_storage_unavailable"
            item.update(state="failed", error=code)
        db.execute(
            "UPDATE vault_operations SET payload=?,updated_at=? WHERE id=?",
            (canonical(value), time.time(), operation),
        )
    state = (
        "completed"
        if all(i["state"] == "completed" for i in value["items"])
        else "partial"
        if any(i["state"] == "failed" for i in value["items"])
        else "running"
    )
    db.execute(
        "UPDATE vault_operations SET state=?,updated_at=? WHERE id=?",
        (state, time.time(), operation),
    )
    row, value = get(db, client, vault, operation)
    return result(row, value)
