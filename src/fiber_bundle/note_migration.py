"""Two-end authorization and current-evidence preflight for one historical note."""

import os
import re
import sqlite3
from dataclasses import asdict
from typing import Any

from . import (
    auth,
    database,
    file_exchange,
    moc_store,
    note_changes,
    note_identities,
    note_identity_store,
    note_inventory,
    note_publications,
    vaults,
)
from . import note_migration_plans as plans
from .config import Config, IntegrityError, InvalidInput, StorageError, contained

LINKS = re.compile(r"\[\[|\]\s*\(|^\s*\[[^\]]+\]:|<(?:a|img|video|audio|source)\b", re.I | re.M)


def endpoints(
    config: Config, db: sqlite3.Connection, client: str, source: str, target: str
) -> tuple[sqlite3.Row, sqlite3.Row]:
    if source == target:
        raise InvalidInput("migration requires different Vaults")
    # Authorize both scopes before reading either inventory or persisted plan.
    for vid in (source, target):
        note_changes.identifier(vid)
        auth.grant(db, client, vid, "query")
        auth.grant(db, client, vid, "add")
    rows = tuple(auth.grant(db, client, vid, "query") for vid in (source, target))
    for row in rows:
        vaults.checked_vault(config, row)
    return rows[0], rows[1]


def inventory_digest(inventory: note_inventory.Inventory) -> str:
    return plans.digest(
        [
            (n.relative_path, n.sha256, n.file_key, n.identity, n.publication_id)
            for n in sorted(inventory.notes, key=lambda n: n.relative_path)
        ]
    )


def associations(config: Config, vid: str, inventory: note_inventory.Inventory) -> dict[str, Any]:
    with moc_store.connection(config) as ledger:
        ids = (
            []
            if ledger is None
            else [
                r[0]
                for r in ledger.execute(
                    "SELECT id FROM mocs WHERE vault_id=? ORDER BY id LIMIT ?",
                    (vid, moc_store.MAX_MOCS + 1),
                )
            ]
        )
    if len(ids) > moc_store.MAX_MOCS:
        raise IntegrityError("migration MOC observation limit exceeded")
    result: dict[str, Any] = {}
    info = inventory.root.stat()
    for mid in ids:
        note_changes.identifier(mid)
        record = moc_store.load(config, vid, mid)
        if record is None or (record.vault_dev, record.vault_ino) != (info.st_dev, info.st_ino):
            raise IntegrityError("migration MOC observation changed")
        result[mid] = asdict(record)
    return result


def observe(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    source: str,
    target: str,
    identity: str,
    revision: int,
    source_path: str,
    target_path: str,
    expected: str,
) -> dict[str, Any]:
    src_row, dst_row = endpoints(config, db, client, source, target)
    src = note_inventory.read(config, db, src_row)
    dst = note_inventory.read(config, db, dst_row)
    note = next(
        (n for n in src.notes if n.identity == identity and n.relative_path == source_path), None
    )
    from .publication_locations import inventory as layout_inventory

    if source_path in layout_inventory(db, source)[0]:
        raise InvalidInput("obsidian layout cross-Vault migration requires a dedicated plan")
    record = next(
        (r for r in note_identity_store.records(config, source) if r.identity == identity), None
    )
    if record is None or note is None or record.revision != revision:
        raise IntegrityError("migration source registration or location conflict")
    note_identities.validate(record)
    if record.publication_id is not None or note.publication_id is not None:
        raise InvalidInput("published bundle migration is not supported by this preflight")
    if note.sha256 != expected or note.file_key != record.file_key:
        raise IntegrityError("migration source evidence changed")
    if any(r.identity == identity for r in note_identity_store.records(config, target)):
        raise IntegrityError("migration target already registers this identity")
    for index in range(1, len(target_path.split("/"))):
        marker = contained(
            dst.root, "/".join(target_path.split("/")[:index]) + "/" + note_publications.MARKER
        )
        if marker.exists():
            raise IntegrityError("migration target cannot be in a publication bundle")
    destination = contained(dst.root, target_path)
    fd = file_exchange.parent_fd(dst.root, target_path)
    try:
        parent = os.fstat(fd)
        try:
            os.stat(destination.name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise IntegrityError("migration target must be absent")
        checked_parent = destination.parent.stat()
        if (checked_parent.st_dev, checked_parent.st_ino) != (parent.st_dev, parent.st_ino):
            raise IntegrityError("migration target parent changed")
    finally:
        os.close(fd)
    source_mocs = associations(config, source, src)
    target_mocs = associations(config, target, dst)
    flags = []
    if LINKS.search(note.text):
        flags.append("source_links_need_review")
    if any(n is not note and LINKS.search(n.text) for n in src.notes):
        flags.append("other_source_links_need_review")
    if any(LINKS.search(n.text) for n in dst.notes):
        flags.append("target_links_need_review")
    for prefix, values in [("source", source_mocs), ("target", target_mocs)]:
        if any(identity in value["members"] for value in values.values()):
            flags.append(prefix + "_moc_membership")
    if "<!-- fiber-moc:" in note.text or re.search(r"^type:\s*moc\s*$", note.text, re.M | re.I):
        flags.append("moc_document_needs_review")
    info, target_info = src.root.stat(), dst.root.stat()
    if info.st_dev != target_info.st_dev:
        flags.append("cross_filesystem_transfer")
    return {
        "schema": 1,
        "client_id": client,
        "source_vault_id": source,
        "target_vault_id": target,
        "note_identity": identity,
        "revision": revision,
        "source_path": source_path,
        "target_path": target_path,
        "source_sha256": expected,
        "source_file_key": list(record.file_key),
        "source_vault_key": [info.st_dev, info.st_ino],
        "target_vault_key": [target_info.st_dev, target_info.st_ino],
        "target_parent_key": [parent.st_dev, parent.st_ino],
        "registration_sha256": plans.digest(asdict(record)),
        "source_inventory_sha256": inventory_digest(src),
        "target_inventory_sha256": inventory_digest(dst),
        "source_mocs_sha256": plans.digest(source_mocs),
        "target_mocs_sha256": plans.digest(target_mocs),
        "review_flags": sorted(flags),
    }


def run(
    config: Config,
    authorization: str,
    source_vault_id: str,
    target_vault_id: str,
    action: str,
    *,
    note_identity: str | None = None,
    revision: int | None = None,
    source_path: str | None = None,
    target_path: str | None = None,
    expected_sha256: str | None = None,
    plan_id: str | None = None,
) -> dict[str, Any]:
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            client = auth.client(db, authorization)["id"]
            endpoints(config, db, client, source_vault_id, target_vault_id)
            if (
                action == "plan"
                and plan_id is None
                and isinstance(note_identity, str)
                and note_identity.startswith("note:")
                and type(revision) is int
                and revision > 0
                and source_path is not None
                and target_path is not None
                and isinstance(expected_sha256, str)
                and note_changes.DIGEST.fullmatch(expected_sha256)
            ):
                note_changes.identifier(note_identity[5:])
                plans.note_path(source_path)
                plans.note_path(target_path)
                args = (
                    config,
                    db,
                    client,
                    source_vault_id,
                    target_vault_id,
                    note_identity,
                    revision,
                    source_path,
                    target_path,
                    expected_sha256,
                )
                value = observe(*args)
                if observe(*args) != value:
                    raise IntegrityError("migration evidence changed during preflight")
                endpoints(config, db, client, source_vault_id, target_vault_id)
                plan_id = plans.save(config, value)
                # A post-save race preserves stale metadata, never an executable claim.
                if observe(*args) != value:
                    raise IntegrityError("migration evidence changed before plan delivery")
                status, changes = "planned", []
            elif (
                action == "check"
                and plan_id is not None
                and all(
                    v is None
                    for v in (note_identity, revision, source_path, target_path, expected_sha256)
                )
            ):
                value = plans.load(config, client, source_vault_id, target_vault_id, plan_id)
                try:
                    args = (
                        config,
                        db,
                        client,
                        source_vault_id,
                        target_vault_id,
                        value["note_identity"],
                        value["revision"],
                        value["source_path"],
                        value["target_path"],
                        value["source_sha256"],
                    )
                    current = observe(*args)
                    again = observe(*args)
                    changes = sorted(
                        k for k in value if value[k] != current[k] or current[k] != again[k]
                    )
                    status = "conflict" if changes else "match"
                except (StorageError, OSError, ValueError, sqlite3.Error):
                    status, changes = "conflict", ["current_evidence_unavailable"]
            else:
                raise InvalidInput("invalid migration preflight action arguments")
            final = endpoints(config, db, client, source_vault_id, target_vault_id)
            for row, name in zip(final, ("source_vault_key", "target_vault_key"), strict=True):
                info = vaults.checked_vault(config, row).stat()
                if [info.st_dev, info.st_ino] != value[name]:
                    raise IntegrityError("migration Vault changed before delivery")
            return {
                "status": status,
                "plan_id": plan_id,
                "plan": value,
                "changed_fields": changes,
                "metadata_only": True,
                "vault_write_performed": False,
                "write_authorization": False,
                "execution_available": False,
            }
        finally:
            db.close()
