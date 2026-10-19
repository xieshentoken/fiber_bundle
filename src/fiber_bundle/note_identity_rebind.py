"""Metadata-only repair of ordinary tracked notes, using one shared batch snapshot."""

import json
import sqlite3
import stat
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import (
    auth,
    note_identities,
    note_identity_store,
    note_index,
    publication_locations,
    publication_rebind,
    vault_directories,
    vault_scope,
)
from .config import Config, IntegrityError
from .inputs import canonical


@dataclass
class Context:
    root: Path
    scope: vault_scope.Scope
    notes: dict[str, note_index.Note]
    records: dict[str, note_identity_store.Registration]
    directories: dict[str, list[int]]
    epoch: int | None


def directory_key(path: Path) -> list[int]:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise IntegrityError("layout_rebind_preview_changed")
    return [info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns]


def context(config: Config, db: sqlite3.Connection, client: str, vault: str) -> Context:
    root, scope, _ = publication_rebind.repair_context(config, db, client, vault)
    # Publication repairs have their own stronger package/source checks. Never reinterpret
    # their missing records or an expert package's internal Markdown as ordinary notes.
    managed_paths: set[str] = set()
    managed_ids: set[str] = set()
    packages: set[str] = set()
    for raw in db.execute("SELECT * FROM publication_locations WHERE vault_id=?", (vault,)):
        plan = json.loads(raw["value"])
        managed_paths.update(plan["mapping"].values())
        if raw["package_root"]:
            packages.add(raw["package_root"])
        for logical in plan["mapping"]:
            managed_ids.add(
                plan.get("identity_overrides", {}).get(
                    logical,
                    publication_locations.note_id(raw["kind"], raw["publication_id"], logical),
                )
            )
    from .attachment_projection import hidden_roots
    from .organization.packages import roots

    packages |= roots(config, vault) | hidden_roots(db, vault)
    notes = note_index.snapshot(
        root,
        set(),
        set(),
        scope=scope,
        hidden_files=managed_paths,
        hidden_roots=packages,
        limits=note_index.Limits(total_bytes=256 * 1024 * 1024),
    )
    records = {
        r.identity: r
        for r in note_identity_store.records(config, vault)
        if r.publication_id is None
        and r.identity not in managed_ids
        and scope.allows(r.relative_path)
    }
    for record in records.values():
        note_identities.validate(record)
        if record.vault_key != tuple(directory_key(root)[:2]):
            raise IntegrityError("layout_note_identity_vault_changed")
    directories = {"": directory_key(root)}
    for path, info in vault_scope.entries(root, scope):
        if stat.S_ISDIR(info.st_mode):
            directories[path.relative_to(root).as_posix()] = directory_key(path)
    epoch = db.execute(
        "SELECT revision FROM graph_permission_epochs WHERE client_id=? AND vault_id=?",
        (client, vault),
    ).fetchone()
    return Context(
        root,
        scope,
        {n.relative_path: n for n in notes},
        records,
        directories,
        epoch[0] if epoch else None,
    )


def issues(ctx: Context) -> list[dict[str, Any]]:
    keys = {n.file_key: n for n in ctx.notes.values()}
    output = []
    for record in ctx.records.values():
        original = keys.get(record.file_key)
        if original and original.relative_path == record.relative_path:
            continue
        found = {
            n.relative_path: "content_hash" for n in ctx.notes.values() if n.sha256 == record.sha256
        }
        if original:
            found = {original.relative_path: "file_identity"}
        elif record.relative_path in ctx.notes:
            found = {record.relative_path: "original_path"}
        if not found:
            continue  # Missing ordinary notes already stay out of the index; retain their history.
        candidates = [{"path": p, "evidence": reason} for p, reason in sorted(found.items())]
        output.append(
            {
                "kind": "note",
                "publication_id": record.identity.removeprefix("note:"),
                "expected_revision": record.revision,
                "source_path": record.relative_path,
                "state": "replaced" if record.relative_path in ctx.notes else "moved",
                "candidates": candidates,
                "suggested_path": candidates[0]["path"] if len(candidates) == 1 else None,
                "can_mark_missing": False,
                "blocks_index": original is None and record.relative_path in ctx.notes,
            }
        )
    return output


def preview(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vault: str,
    identity: str,
    revision: int,
    target: str,
    ctx: Context,
) -> dict[str, Any]:
    record = ctx.records.get(identity)
    note = ctx.notes.get(target)
    if not record or record.revision != revision or not note or note.file_key is None:
        raise IntegrityError("layout_rebind_preview_changed")
    if not ctx.scope.allows(target):
        raise IntegrityError("layout_rebind_scope_denied")
    if any(n.file_key == record.file_key and n.relative_path != target for n in ctx.notes.values()):
        raise IntegrityError("layout_rebind_original_still_present")
    if any(r.file_key == note.file_key and r.identity != identity for r in ctx.records.values()):
        raise IntegrityError("layout_rebind_identity_collision")
    current = note_index.read_note(ctx.root, target, note_index.Limits().note_bytes)
    if (note.sha256, note.file_key) != (current.sha256, current.file_key):
        raise IntegrityError("layout_rebind_preview_changed")
    after = replace(
        record,
        relative_path=target,
        sha256=note.sha256,
        file_key=note.file_key,
        revision=record.revision + 1,
    )
    value = {
        "client_id": client,
        "vault_id": vault,
        "before": asdict(record),
        "after": asdict(after),
        "directories": ctx.directories,
        "scope": ctx.scope.wire(),
        "epoch": ctx.epoch,
        "expires_at": time.time() + 600,
    }
    operation = str(uuid4())
    db.execute(
        "INSERT INTO vault_operations VALUES(?,?,?,'prepared',?,?,?)",
        (operation, vault, "note_identity_rebind", canonical(value), time.time(), time.time()),
    )
    return {
        "operation_id": operation,
        "modified_notes": [target] if note.sha256 != record.sha256 else [],
    }


def apply(
    config: Config, db: sqlite3.Connection, client: str, vault: str, operation: str, ctx: Context
) -> None:
    for permission in ("query", "add"):
        auth.grant(db, client, vault, permission)
    row = db.execute(
        "SELECT * FROM vault_operations WHERE id=? AND vault_id=? AND kind='note_identity_rebind'",
        (operation, vault),
    ).fetchone()
    if row is None:
        raise auth.Denied(404)
    value = json.loads(row["payload"])
    if value["client_id"] != client:
        raise auth.Denied(404)
    if row["state"] == "completed":
        return
    before, after = value["before"], value["after"]
    checked_root, checked_scope, _ = vault_directories.vault_root(config, db, vault)
    if (
        tuple(directory_key(checked_root)[:2]) != tuple(before["vault_key"])
        or checked_scope.wire() != value["scope"]
    ):
        raise IntegrityError("layout_rebind_preview_changed")
    existing = next(
        (r for r in note_identity_store.records(config, vault) if r.identity == before["identity"]),
        None,
    )
    # A sidecar commit may precede the main-journal acknowledgement on interruption.
    if existing and canonical(asdict(existing)) == canonical(after):
        db.execute("UPDATE vault_operations SET state='completed' WHERE id=?", (operation,))
        return
    if time.time() > value["expires_at"]:
        raise IntegrityError("placement_preview_expired")
    if (
        existing is None
        or canonical(asdict(existing)) != canonical(before)
        or ctx.scope.wire() != value["scope"]
        or ctx.epoch != value["epoch"]
    ):
        raise IntegrityError("layout_rebind_preview_changed")
    # Metadata-only commits do not change these directories. External creation, removal,
    # rename, or atomic replacement invalidates the remaining frozen batch immediately.
    for path, expected in value["directories"].items():
        if directory_key(ctx.root / path) != expected:
            raise IntegrityError("layout_rebind_preview_changed")
    current = note_index.read_note(ctx.root, after["relative_path"], note_index.Limits().note_bytes)
    if current.sha256 != after["sha256"] or list(current.file_key or ()) != after["file_key"]:
        raise IntegrityError("layout_rebind_preview_changed")
    changed = replace(
        existing,
        relative_path=current.relative_path,
        sha256=current.sha256,
        file_key=tuple(after["file_key"]),
        revision=after["revision"],
    )
    note_identity_store.save(config, vault, client, changed, expected_revision=existing.revision)
    db.execute(
        "UPDATE vault_operations SET state='completed',updated_at=? WHERE id=?",
        (time.time(), operation),
    )
