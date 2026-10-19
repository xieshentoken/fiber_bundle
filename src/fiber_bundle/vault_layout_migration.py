"""Optional per-publication conversion. Preview does not hide or change legacy notes."""

import base64
import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import (
    auth,
    note_identity_store,
    note_inventory,
    note_placement,
    publication_layout,
    publication_links,
    publication_locations,
    vault_directories,
)
from .config import Config, IntegrityError, InvalidInput, contained, make_directory
from .inputs import canonical
from .objects import digest
from .publication_file_io import read, tree

SOURCES = {
    "article": (
        "SELECT p.id AS identity,p.relative_path,p.manifest_json FROM "
        "article_publications p WHERE p.vault_id=? AND p.state='published'"
    ),
    "document": (
        "SELECT p.run_id AS identity,p.relative_path,p.manifest_json FROM "
        "document_publications p JOIN execution_runs r ON r.id=p.run_id WHERE "
        "r.vault_id=? AND p.state='published'"
    ),
    "media": (
        "SELECT p.job_id AS identity,p.relative_path,p.manifest_json FROM "
        "media_publications p JOIN media_tasks m ON m.id=p.job_id WHERE "
        "m.vault_id=? AND p.state='published'"
    ),
    "expert": (
        "SELECT p.task_id AS identity,p.relative_path,p.manifest_json FROM "
        "source_expert_publications p JOIN source_imports i ON i.id=p.task_id "
        "WHERE i.vault_id=? AND p.state='published'"
    ),
}


def sources(config: Config, db: sqlite3.Connection, vault: str) -> list[tuple[str, dict[str, Any]]]:
    result = [
        (kind, dict(row))
        for kind, sql in SOURCES.items()
        for row in db.execute(sql + " ORDER BY identity LIMIT 1001", (vault,))
    ]
    from .channels import store as channel_store

    with channel_store.readonly(config) as side:
        rows = (
            side.execute(
                "SELECT value FROM channel_intake_records WHERE state='stored' LIMIT 1001"
            ).fetchall()
            if side
            else []
        )
    if len(rows) > 1000:
        raise IntegrityError("layout_migration_candidate_limit")
    for raw in rows:
        value = json.loads(raw[0])
        if value.get("target_vault_id") != vault:
            continue
        for link in value.get("links", []):
            publication = link.get("publication")
            if publication and publication.get("state") == "published":
                result.append(
                    (
                        "intake",
                        {
                            "identity": publication["id"],
                            "relative_path": publication["relative_path"],
                            "manifest_json": canonical(link["files"]),
                            "intake_source": {
                                "source_version_id": link["receipt"]["source_version_id"],
                                "source_url": link.get("url"),
                                "review_id": link["review"]["id"],
                            },
                        },
                    )
                )
    if len(result) > 1000:
        raise IntegrityError("layout_migration_candidate_limit")
    return result


def candidates(
    config: Config, db: sqlite3.Connection, client: str, vault: str
) -> list[dict[str, Any]]:
    note_placement.context(config, db, client, vault)
    result = []
    for kind, row in sources(config, db, vault):
        if not publication_locations.get(db, kind, row["identity"]):
            result.append(
                {
                    "kind": kind,
                    "publication_id": row["identity"],
                    "source_path": row["relative_path"],
                }
            )
    return result


def preview(
    config: Config, db: sqlite3.Connection, client: str, vault: str, kind: str, identity: str
) -> dict[str, Any]:
    note_placement.guard(db, vault)
    root, scope, policy = note_placement.context(config, db, client, vault)
    if kind not in {*SOURCES, "intake"}:
        raise InvalidInput("layout_migration_managed_source_required")
    source = next(
        (
            row
            for source_kind, row in sources(config, db, vault)
            if source_kind == kind and row["identity"] == identity
        ),
        None,
    )
    if source is None or publication_locations.get(db, kind, identity):
        raise InvalidInput("layout_migration_managed_source_required")
    relative = source["relative_path"]
    if not scope.allows(relative):
        raise InvalidInput("layout_migration_scope_denied")
    inventory = note_inventory.read(config, db, auth.grant(db, client, vault, "query"))
    files = tree(root, relative, scope)
    if "note.md" not in files:
        raise IntegrityError("layout_migration_entry_missing")
    # Only a ledger-bound tree is eligible; preserve current bodies, flag human changes.
    ledger = json.loads(source["manifest_json"])
    expected = ledger.get("files", ledger)
    known = set(expected) - {"vault_key"}
    known.add(".fiber-publication.json") if kind == "article" else None
    if set(files) != known:
        raise IntegrityError("layout_migration_untracked_files_require_review")
    modified = [n for n, b in files.items() if n in expected and digest(b) != expected[n]["sha256"]]
    if any(not n.endswith(".md") for n in modified):
        raise IntegrityError("layout_migration_resource_changed")
    planned = publication_layout.prepare(config, db, kind, identity, vault, files, persist=False)
    if planned is None:
        raise InvalidInput("placement_active_policy_required")
    if kind == "intake":
        planned["plan"]["intake_source"] = source["intake_source"]
    mapping = {relative + "/" + n: p for n, p in planned["plan"]["mapping"].items()}
    updates, unresolved = note_placement.plan_links(root, scope, inventory, mapping)
    source_updates = {u["path"]: u for u in updates if u["path"].startswith(relative + "/")}
    # Keep the old tree intact. New bodies receive the same controlled outbound edits.
    for logical, path in planned["plan"]["mapping"].items():
        prior = relative + "/" + logical
        if logical.endswith(".md") and prior in source_updates:
            rewritten = base64.b64decode(source_updates[prior]["after"]).decode("utf-8-sig")
            if (
                planned["package_root"] is None
                and policy["settings"]["image_link_style"] == "obsidian"
            ):
                import posixpath

                image_names = {
                    posixpath.relpath(dest, posixpath.dirname(path)): dest
                    for n, dest in planned["plan"]["mapping"].items()
                    if Path(n).suffix.lower() in publication_layout.IMAGES
                }
                rewritten = publication_links.images(rewritten, image_names, path, "obsidian")
            planned["plan"]["outputs"][path] = base64.b64encode(rewritten.encode()).decode()
            planned["plan"]["published_hashes"][path] = digest(rewritten.encode())
        if logical in modified:
            planned["plan"]["published_hashes"][path] = expected[logical]["sha256"]
    # Actual write hashes are separate from the original baseline for preserved human revisions.
    planned["plan"]["write_hashes"] = {
        p: digest(base64.b64decode(b)) for p, b in planned["plan"]["outputs"].items()
    }
    planned["plan"]["retained_source_root"] = relative
    for note in inventory.notes:
        if note.relative_path in mapping and note.identity and note.identity.startswith("note:"):
            planned["plan"].setdefault("identity_overrides", {})[
                note.relative_path.removeprefix(relative + "/")
            ] = note.identity
    source_files = {
        relative + "/" + n: {"sha256": digest(b), "file_key": read(root, relative + "/" + n)[1]}
        for n, b in files.items()
    }
    token = str(uuid4())
    now = time.time()
    value = {
        "client_id": client,
        "vault_id": vault,
        "root_key": planned["plan"]["root_key"],
        "scope": scope.wire(),
        "policy_revision": policy["revision"],
        "policy_binding": planned["plan"]["policy_binding"],
        "kind": kind,
        "publication_id": identity,
        "location_revision": 0,
        "source_root": relative,
        "target_root": planned["package_root"] or planned["entry_note_path"],
        "moves": mapping,
        "updates": [u for u in updates if not u["path"].startswith(relative + "/")],
        "source_files": source_files,
        "unresolved_links": unresolved,
        "moc_choices": [],
        "association": None,
        "association_applied": False,
        "exchanges": {},
        "move_applied": False,
        "moc_snapshot": {
            mid: asdict(r) for mid, r in note_placement.records(config, vault).items() if r
        },
        "registrations": [asdict(r) for r in note_identity_store.records(config, vault)],
        "action": "layout_migration",
        "expires_at": now + 600,
        "location": planned,
        "source_ledger": source,
        "modified_notes": modified,
        "note_snapshot": [
            (n.relative_path, n.sha256, n.identity, n.publication_id, list(n.file_key or ()))
            for n in inventory.notes
        ],
        "backup_complete": False,
    }
    db.execute(
        "INSERT INTO vault_operations VALUES(?,?,?,'prepared',?,?,?)",
        (token, vault, "layout_migration", canonical(value), now, now),
    )
    return result(token, "prepared", value)


def result(identity: str, state: str, value: dict[str, Any]) -> dict[str, Any]:
    return {
        **note_placement.result(identity, state, value),
        "modified_notes": value["modified_notes"],
        "retained_source_root": value["source_root"],
        "backup_complete": value["backup_complete"],
        "ready": not value["unresolved_links"],
    }


def apply(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vault: str,
    operation: str,
    *,
    reconcile: bool = False,
) -> dict[str, Any]:
    from . import note_placement_execution as execution
    from .publication_commit import apply as commit

    row = db.execute(
        "SELECT * FROM vault_operations WHERE id=? AND vault_id=? AND kind='layout_migration'",
        (operation, vault),
    ).fetchone()
    if row is None:
        raise auth.Denied(404)
    value = json.loads(row["payload"])
    if value["client_id"] != client:
        raise auth.Denied(404)
    root, scope, policy = note_placement.context(config, db, client, vault)
    if row["state"] == "completed":
        return result(operation, "completed", value)
    if row["state"] == "cancelled" or value["unresolved_links"]:
        raise IntegrityError("layout_migration_preview_not_ready")
    if row["state"] != "prepared" and not reconcile:
        raise IntegrityError("placement_reconciliation_required")
    if (
        policy["revision"] != value["policy_revision"]
        or value["root_key"] != value["location"]["plan"]["root_key"]
        or vault_directories.key(root) != value["root_key"]
    ):
        raise IntegrityError("layout_migration_policy_changed")
    current_source = next(
        (
            source
            for source_kind, source in sources(config, db, vault)
            if source_kind == value["kind"] and source["identity"] == value["publication_id"]
        ),
        None,
    )
    if current_source != value["source_ledger"]:
        raise IntegrityError("layout_migration_source_changed")
    for path, evidence in value["source_files"].items():
        raw, key = read(root, path)
        if digest(raw) != evidence["sha256"] or key != evidence["file_key"]:
            raise IntegrityError("layout_migration_source_changed")
    if row["state"] == "prepared":
        if time.time() > value["expires_at"]:
            raise IntegrityError("placement_preview_expired")
        inv = note_inventory.read(config, db, auth.grant(db, client, vault, "query"))
        observed = [
            (n.relative_path, n.sha256, n.identity, n.publication_id, list(n.file_key or ()))
            for n in inv.notes
        ]
        if canonical(observed) != canonical(value["note_snapshot"]):
            raise IntegrityError("placement_note_changed")
    execution.persist(db, operation, value, "applying")
    try:
        if not value["backup_complete"]:
            backup = contained(config.state_root, "layout-migration-backups/" + operation)
            make_directory(backup)
            # Actual independent snapshot, never an asserted or user-supplied backup flag.
            target = backup / "metadata.sqlite3"
            if target.exists():
                raise IntegrityError("layout_migration_backup_requires_reconciliation")
            with sqlite3.connect(target) as copy:
                db.backup(copy)
            target.chmod(0o600)
            from .query_policy_store import write_private

            for path, evidence in value["source_files"].items():
                data, key = read(root, path)
                if digest(data) != evidence["sha256"] or key != evidence["file_key"]:
                    raise IntegrityError("layout_migration_source_changed")
                copied = contained(
                    backup, "source-files/" + path.removeprefix(value["source_root"] + "/")
                )
                make_directory(copied.parent)
                write_private(copied, data)
            write_private(
                backup / "source.json",
                canonical(
                    {
                        "source": value["source_ledger"],
                        "files": value["source_files"],
                        "location": value["location"],
                    }
                ).encode(),
            )
            value["backup_complete"] = True
            execution.persist(db, operation, value, "applying")
        location = publication_locations.get(db, value["kind"], value["publication_id"])
        if location is None:
            planned = value["location"]
            db.execute(
                "INSERT INTO publication_locations VALUES(?,?,?,?,?,1,'prepared',?)",
                (
                    value["kind"],
                    value["publication_id"],
                    vault,
                    planned["entry_note_path"],
                    planned["package_root"],
                    canonical(planned["plan"]),
                ),
            )
        commit(config, db, value["kind"], value["publication_id"], client)
        for update in value["updates"]:
            execution.exchange(config, db, operation, value, root, scope, update)
        execution.complete(config, db, operation, value, root)
    except BaseException:
        execution.persist(db, operation, value, "reconcile")
        raise
    return result(operation, "completed", value)
