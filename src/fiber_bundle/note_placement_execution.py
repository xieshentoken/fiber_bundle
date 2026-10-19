"""Archive execution: CAS exchanges, exclusive move, durable reconciliation and backups."""

import base64
import json
import os
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import (
    auth,
    database,
    file_exchange,
    moc_store,
    note_identity_store,
    note_inventory,
    note_placement,
    publication_links,
    publication_locations,
    vault_directories,
    vaults,
)
from .config import Config, IntegrityError, contained
from .inputs import canonical
from .objects import digest
from .publication_file_io import read


def persist(db: sqlite3.Connection, identity: str, value: dict[str, Any], state: str) -> None:
    db.execute(
        "UPDATE vault_operations SET payload=?,state=?,updated_at=? WHERE id=?",
        (canonical(value), state, time.time(), identity),
    )


def check(config: Config, db: sqlite3.Connection, value: dict[str, Any]) -> tuple[Path, Any]:
    root, scope, policy = note_placement.context(config, db, value["client_id"], value["vault_id"])
    current = db.execute(
        "SELECT value FROM vault_save_policies WHERE vault_id=?", (value["vault_id"],)
    ).fetchone()
    if (
        vault_directories.key(root) != value["root_key"]
        or scope.wire() != value["scope"]
        or policy["revision"] != value["policy_revision"]
        or json.loads(current[0]) != value["policy_binding"]
    ):
        raise IntegrityError("placement_policy_or_vault_changed")
    location = publication_locations.get(db, value["kind"], value["publication_id"])
    if location is None or location["revision"] not in {
        value["location_revision"],
        value["location_revision"] + 1,
    }:
        raise IntegrityError("placement_location_changed")
    for path in (
        set(value["moves"]) | set(value["moves"].values()) | {u["path"] for u in value["updates"]}
    ):
        vaults.require_scope(
            config, auth.grant(db, value["client_id"], value["vault_id"], "add"), path
        )
    mocs = {
        mid: asdict(r) for mid, r in note_placement.records(config, value["vault_id"]).items() if r
    }
    wanted = dict(value["moc_snapshot"])
    if value["association"]:
        mid = value["association"]["moc_id"]
        if mocs.get(mid) == value["association"]["after"]:
            wanted[mid] = value["association"]["after"]
        elif value["association_applied"]:
            raise IntegrityError("placement_moc_changed")
    if mocs != wanted:
        raise IntegrityError("placement_moc_changed")
    return root, scope


def exchange(
    config: Config,
    db: sqlite3.Connection,
    operation: str,
    value: dict[str, Any],
    root: Path,
    scope: Any,
    update: dict[str, Any],
) -> None:
    path = update["target"] if value["move_applied"] else update["path"]
    key = digest(update["path"].encode())[:16]
    stage = ".fiber-placement-" + operation + "-" + key
    prior = value["exchanges"].get(update["path"])
    with vault_directories.opened_parent(
        root, scope, path.rsplit("/", 1)[0] if "/" in path else ""
    ) as (_, target_fd):
        # Staging and original bytes remain in the Vault root as a private recovery copy.
        with vault_directories.opened_parent(root, scope, "") as (_, stage_fd):
            name = path.rsplit("/", 1)[-1]
            current, current_key = read(root, path)
            if prior is None:
                if digest(current) != update["before_sha256"] or current_key != update["file_key"]:
                    raise IntegrityError("placement_note_changed")
                handle = os.open(
                    stage,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=stage_fd,
                )
                try:
                    info = os.fstat(handle)
                    prior = {
                        "stage": stage,
                        "after_key": [info.st_dev, info.st_ino],
                        "state": "claimed",
                    }
                    value["exchanges"][update["path"]] = prior
                    persist(db, operation, value, "applying")
                    with os.fdopen(handle, "wb", closefd=False) as stream:
                        stream.write(base64.b64decode(update["after"], validate=True))
                        stream.flush()
                        os.fsync(handle)
                finally:
                    os.close(handle)
                os.fsync(stage_fd)
            if current_key[:2] == prior["after_key"] and digest(current) == update["after_sha256"]:
                before, oldkey = read(root, stage)
                if digest(before) != update["before_sha256"] or oldkey != update["file_key"]:
                    raise IntegrityError("placement_backup_changed")
                prior["state"] = "exchanged"
                persist(db, operation, value, "applying")
                return
            staged, staged_key = read(root, stage)
            if (
                digest(staged) != update["after_sha256"]
                or staged_key[:2] != prior["after_key"]
                or digest(current) != update["before_sha256"]
                or current_key != update["file_key"]
            ):
                raise IntegrityError("placement_exchange_requires_reconciliation")
            # Check the opened target immediately before an exchange; preserve the displaced file.
            again, _ = file_exchange.read(target_fd, name)
            if digest(again) != update["before_sha256"]:
                raise IntegrityError("placement_note_changed")
            file_exchange.rename(stage_fd, stage, target_fd, name, exchange=True)
            old, oldkey = read(root, stage)
            if digest(old) != update["before_sha256"] or oldkey != update["file_key"]:
                raise IntegrityError("placement_concurrent_edit_preserved")
            prior["state"] = "exchanged"
            persist(db, operation, value, "applying")


def move(
    db: sqlite3.Connection, operation: str, value: dict[str, Any], root: Path, scope: Any
) -> None:
    if value["action"] == "associate_only":
        return
    source, target = value["source_root"], value["target_root"]
    old, new = contained(root, source), contained(root, target)
    updates = {u["path"]: u for u in value["updates"]}
    old_exists = old.exists() or old.is_symlink()
    new_exists = new.exists() or new.is_symlink()
    if old_exists and new_exists or not old_exists and not new_exists:
        raise IntegrityError("placement_move_ambiguous")
    if len(value["source_files"]) > 1 and any(
        p.startswith(source + "/") for p in value["source_files"]
    ):
        from .publication_file_io import tree

        actual_root = target if new_exists else source
        if set(tree(root, actual_root, scope)) != {
            p.removeprefix(source + "/") for p in value["source_files"]
        }:
            raise IntegrityError("placement_package_members_changed")
    for path, evidence in value["source_files"].items():
        current = value["moves"][path] if new_exists else path
        raw, key = read(root, current)
        update = updates.get(path)
        expected_hash = update["after_sha256"] if update else evidence["sha256"]
        expected_key = value["exchanges"][path]["after_key"] if update else evidence["file_key"][:2]
        if digest(raw) != expected_hash or key[:2] != expected_key:
            raise IntegrityError("placement_source_changed")
    if old_exists:
        with vault_directories.opened_parent(
            root,
            scope,
            str(Path(source).parent).replace(".", "")
            if "/" not in source
            else source.rsplit("/", 1)[0],
        ) as (_, source_fd):
            with vault_directories.opened_parent(root, scope, target.rsplit("/", 1)[0]) as (
                _,
                target_fd,
            ):
                file_exchange.rename(
                    source_fd, Path(source).name, target_fd, Path(target).name, exchange=False
                )
    value["move_applied"] = True
    persist(db, operation, value, "applying")


def complete(
    config: Config, db: sqlite3.Connection, operation: str, value: dict[str, Any], root: Path
) -> None:
    updates = {u["path"]: u for u in value["updates"]}
    candidates = list(value["all_paths"]) if "all_paths" in value else []
    # Baseline hashes follow only controlled link rewrites; human differences remain revisions.
    from .vault_scope import entries, for_vault

    scope = for_vault(root, auth.grant(db, value["client_id"], value["vault_id"], "query"))
    import stat

    candidates.extend(
        p.relative_to(root).as_posix() for p, i in entries(root, scope) if stat.S_ISREG(i.st_mode)
    )
    candidates.extend(value["moves"])
    with database.transaction(db):
        for row in note_placement.locations(db, value["vault_id"]):
            if row is None:
                continue
            plan = row["plan"]
            changed = False
            for logical, oldpath in list(plan["mapping"].items()):
                newpath = value["moves"].get(oldpath, oldpath)
                if oldpath in updates:
                    raw = base64.b64decode(plan["outputs"][oldpath])
                    baseline, _ = publication_links.relocate(
                        raw.decode("utf-8-sig"), root, oldpath, newpath, candidates, value["moves"]
                    )
                    if raw.startswith(b"\xef\xbb\xbf"):
                        baseline = "\ufeff" + baseline
                    plan["outputs"][oldpath] = base64.b64encode(baseline.encode()).decode()
                    plan["published_hashes"][oldpath] = digest(baseline.encode())
                    if oldpath == row["entry_note_path"] and "source_spans" in plan:
                        plan["source_spans"].update(
                            status="relocated_links_offsets_unavailable",
                            published_units=None,
                            published_note_sha256=digest(baseline.encode()),
                        )
                if oldpath in updates or newpath != oldpath:
                    changed = True
                    raw, key = read(root, newpath)
                    for field in (
                        "outputs",
                        "published_hashes",
                        "claims",
                        "file_keys",
                        "write_hashes",
                    ):
                        if field not in plan:
                            continue
                        if oldpath in plan[field]:
                            item = plan[field].pop(oldpath)
                            plan[field][newpath] = item
                    plan["claims"][newpath] = key[:2]
                    plan["file_keys"][newpath] = key
                    if "write_hashes" in plan:
                        plan["write_hashes"][newpath] = digest(raw)
                    plan["mapping"][logical] = newpath
            if changed:
                package = (
                    value["target_root"]
                    if row["kind"] == value["kind"]
                    and row["publication_id"] == value["publication_id"]
                    and row["package_root"]
                    else row["package_root"]
                )
                entry = value["moves"].get(row["entry_note_path"], row["entry_note_path"])
                db.execute(
                    "UPDATE publication_locations SET "
                    "value=?,entry_note_path=?,package_root=?,revision=revision+1 WHERE "
                    "kind=? AND publication_id=?",
                    (canonical(plan), entry, package, row["kind"], row["publication_id"]),
                )
        # Sidecar CAS writes are journaled and idempotent across the main DB transaction.
        for before in value["registrations"]:
            oldpath = before["relative_path"]
            path = value["moves"].get(oldpath, oldpath)
            if oldpath not in updates and path == oldpath:
                continue
            current = next(
                (
                    r
                    for r in note_identity_store.records(config, value["vault_id"])
                    if r.identity == before["identity"]
                ),
                None,
            )
            raw, key = read(root, path)
            after = note_identity_store.Registration(
                before["identity"],
                before["publication_id"],
                path,
                digest(raw),
                tuple(key),
                tuple(before["vault_key"]),
                before["revision"] + 1,
            )
            if current == after:
                continue
            if current is None or asdict(current) != {
                **before,
                "file_key": tuple(before["file_key"]),
                "vault_key": tuple(before["vault_key"]),
            }:
                raise IntegrityError("placement_registered_identity_changed")
            note_identity_store.save(
                config,
                value["vault_id"],
                value["client_id"],
                after,
                expected_revision=before["revision"],
            )
        persist(db, operation, value, "completed")
    from .note_search_jobs import record

    record(config, value["vault_id"], "pending", reason="document_publication")


def apply(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vault: str,
    operation: str,
    *,
    reconcile: bool = False,
) -> dict[str, Any]:
    row = db.execute(
        "SELECT * FROM vault_operations WHERE id=? AND vault_id=? AND kind='note_placement'",
        (operation, vault),
    ).fetchone()
    if row is None:
        raise auth.Denied(404)
    value = json.loads(row["payload"])
    if value["client_id"] != client:
        raise auth.Denied(404)
    if row["state"] == "completed":
        check(config, db, value)
        return note_placement.result(operation, row["state"], value)
    if (
        row["state"] == "cancelled"
        or not note_placement.result(operation, row["state"], value)["ready"]
    ):
        raise IntegrityError("placement_preview_not_ready")
    if row["state"] != "prepared" and not reconcile:
        raise IntegrityError("placement_reconciliation_required")
    root, scope = check(config, db, value)
    from .moc_placement_projection import check as check_moc_files

    check_moc_files(config, value, operation)
    if row["state"] == "prepared":
        if time.time() > value["expires_at"]:
            raise IntegrityError("placement_preview_expired")
        inv = note_inventory.read(config, db, auth.grant(db, client, vault, "query"))
        observed_notes = [
            (n.relative_path, n.sha256, n.identity, n.publication_id, list(n.file_key or ()))
            for n in inv.notes
        ]
        if canonical(observed_notes) != canonical(value["note_snapshot"]):
            raise IntegrityError("placement_note_changed")
    persist(db, operation, value, "applying")
    try:
        if value["association"]:
            association = value["association"]
            after = moc_store.Record(**association["after"])
            current = moc_store.load(config, vault, association["moc_id"])
            if current != after:
                moc_store.save(
                    config,
                    vault,
                    association["moc_id"],
                    client,
                    after,
                    "placement_manual_link",
                    association["before"]["revision"],
                )
            value["association_applied"] = True
            persist(db, operation, value, "applying")
        if (
            reconcile
            and value["target_root"]
            and not contained(root, value["source_root"]).exists()
            and contained(root, value["target_root"]).exists()
        ):
            move(db, operation, value, root, scope)
        for update in value["updates"]:
            check(config, db, value)
            exchange(config, db, operation, value, root, scope, update)
        check(config, db, value)
        move(db, operation, value, root, scope)
        complete(config, db, operation, value, root)
        from .moc_placement_projection import finalize

        finalize(config, db, operation, value, root)
    except BaseException:
        persist(db, operation, value, "reconcile")
        raise
    if value["action"] == "associate_only" and value["association"]:
        from .note_placement import prepare_after_moc

        prepare_after_moc(config, db, client, vault, list(value["association"]["after"]["members"]))
    return note_placement.result(operation, "completed", value)
