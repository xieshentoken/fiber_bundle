"""Explicit interruption recovery and ownership-checked rollback; no historical event replay."""

import os
from pathlib import Path
from typing import Any

from . import database, publication_file_io, vault_directories, vault_merge_guard
from . import vault_merge_journal as journal
from .config import Config, IntegrityError, InvalidInput, StorageError
from .executor_container import run_lock
from .objects import digest


def startup(config: Config) -> None:
    with database.locked(config), database.connect(config.state_root) as db:
        for row in db.execute(
            "SELECT id FROM vault_operations WHERE kind='vault_merge' AND state NOT IN "
            "('prepared','complete','rolled_back','cancelled','reconcile')"
        ).fetchall():
            value, _ = journal.load(db, row[0])
            value["reason"] = "vault_merge_interrupted_explicit_reconciliation_required"
            journal.save(db, row[0], value, "reconcile")


def tick(config: Config) -> bool:
    from .vault_merge_execution import advance

    with database.connect(config.state_root) as db:
        row = db.execute(
            "SELECT id FROM vault_operations WHERE kind='vault_merge' AND state IN "
            "('pausing','copying','originals','rebinding','switching','index') ORDER BY "
            "created_at LIMIT 1"
        ).fetchone()
    if row is None:
        return False
    try:
        advance(config, row[0])
    except (StorageError, OSError):
        pass  # The execution journal retains the failure; never replay an unknown effect here.
    return True


def roots(config: Config, db: Any, value: dict[str, Any]) -> Path:
    for inventory in value["plan"]["inventories"]:
        context = inventory["context"]
        root, _, _ = vault_directories.vault_root(config, db, context["vault_id"])
        if str(root) != context["root"] or vault_directories.key(root) != context["root_key"]:
            raise IntegrityError("vault_merge_root_changed")
    return Path(value["plan"]["inventories"][-1]["context"]["root"])


def expected_bytes(config: Config, value: dict[str, Any], item: dict[str, Any]) -> bytes:
    from .vault_merge_files import projected

    if item.get("storage") == "object":
        from .objects import read_object

        descriptor = item["source"]
        return read_object(
            config.state_root, descriptor["path"], descriptor["sha256"], descriptor["bytes"]
        )
    payload = projected(value["plan"], item)
    if payload is None:
        source = next(
            s
            for s in value["plan"]["inventories"]
            if s["context"]["vault_id"] == item["source_vault_id"]
        )
        payload, _ = publication_file_io.read(Path(source["context"]["root"]), item["source_path"])
    if digest(payload) != item["projected_sha256"]:
        raise IntegrityError("vault_merge_frozen_source_changed")
    return payload


def owned(config: Config, db: Any, value: dict[str, Any]) -> list[tuple[str, bytes, list[Any]]]:
    """Validate every claimed file before removing any; unclaimed paths never become ours."""
    root = roots(config, db, value)
    checked = []
    for index, progress in value["progress"].items():
        if progress["state"] == "removed":
            continue
        item = value["plan"]["files"][int(index)]
        path = item["target_path"]
        try:
            raw, key = publication_file_io.read(root, path)
        except FileNotFoundError:
            if progress.get("removing"):
                continue
            raise IntegrityError("vault_merge_owned_file_missing") from None
        if key[:2] != progress["key"][:2]:
            raise IntegrityError("vault_merge_owned_file_identity_changed")
        if progress["state"] == "copied":
            if key != progress["key"] or digest(raw) != progress["sha256"]:
                raise IntegrityError("vault_merge_owned_file_edited")
        else:
            expected = expected_bytes(config, value, item)
            if len(raw) > len(expected) or raw != expected[: len(raw)]:
                raise IntegrityError("vault_merge_partial_file_edited")
        checked.append((index, raw, key))
    return checked


def cancel(config: Config, identity: str, plan_sha: str) -> dict[str, Any]:
    with (
        run_lock(config, identity),
        database.locked(config),
        database.connect(config.state_root) as db,
    ):
        value, state = journal.load(db, identity)
        if value["plan"]["plan_sha256"] != plan_sha or state in {
            "complete",
            "rolled_back",
            "cancelled",
        }:
            raise InvalidInput("vault_merge_cancel_confirmation_changed")
        value["cancel_requested"] = True
        value["reason"] = "vault_merge_cancelled_effects_preserved_until_rollback"
        journal.save(db, identity, value, "cancelled" if state == "prepared" else "reconcile")
        return journal.view(db, identity)


def resume(config: Config, identity: str, plan_sha: str) -> dict[str, Any]:
    from .vault_merge_execution import authorize

    with (
        run_lock(config, identity),
        vault_merge_guard.executing(identity),
        database.locked(config),
        database.connect(config.state_root) as db,
    ):
        value, state = journal.load(db, identity)
        if (
            state != "reconcile"
            or value["plan"]["plan_sha256"] != plan_sha
            or value.get("cancel_requested")
        ):
            raise InvalidInput("vault_merge_resume_confirmation_changed")
        if value.get("rollback_started"):
            raise InvalidInput("vault_merge_rollback_must_finish")
        authorize(config, db, identity, value)
        root = roots(config, db, value)
        checked = owned(config, db, value)
        # Prove frozen source bytes, target baseline and all current copied bytes before continuing.
        for source in value["plan"]["inventories"]:
            for item in source["files"]:
                raw, key = publication_file_io.read(Path(source["context"]["root"]), item["path"])
                if digest(raw) != item["sha256"] or key[:2] != [item["dev"], item["ino"]]:
                    raise IntegrityError("vault_merge_frozen_file_changed")
        for index, progress in list(value["progress"].items()):
            if (
                progress.get("removing")
                and not (root / value["plan"]["files"][int(index)]["target_path"]).exists()
            ):
                del value["progress"][index]
        for index, raw, key in checked:
            progress = value["progress"][index]
            if progress["state"] != "writing":
                continue
            item = value["plan"]["files"][int(index)]
            if digest(raw) == item["projected_sha256"]:
                value["progress"][index] = {"state": "copied", "key": key, "sha256": digest(raw)}
            else:
                progress["removing"] = True
                journal.save(db, identity, value, state)
                publication_file_io.remove_owned(root, item["target_path"], key, digest(raw))
                del value["progress"][index]
        value.pop("reason", None)
        journal.save(db, identity, value, value["phase"])
        return journal.view(db, identity)


def rollback(config: Config, identity: str, plan_sha: str) -> dict[str, Any]:
    from . import vault_merge_rollback

    with run_lock(config, identity), vault_merge_guard.executing(identity):
        with database.locked(config), database.connect(config.state_root) as db:
            value, state = journal.load(db, identity)
            if value["plan"]["plan_sha256"] != plan_sha or state not in {
                "reconcile",
                "complete",
                "rolling_back",
            }:
                raise InvalidInput("vault_merge_rollback_confirmation_changed")
            root = roots(config, db, value)
            owned(config, db, value)  # A single edited file blocks all destructive effects.
            vault_merge_rollback.validate(config, db, identity, value)
            value.setdefault(
                "rollback_main_snapshot",
                vault_merge_rollback.main_snapshot(db, value["plan"]["selection"]),
            )
            value["rollback_started"] = True
            value["cancel_requested"] = True
            journal.save(db, identity, value, "rolling_back")
            if not value.get("bindings_rolled_back"):
                vault_merge_rollback.bindings(config, db, identity, value)
            vault_merge_rollback.entrances(config, db, identity, value)
        # Remove only independently claimed unchanged files, journaling each removal.
        for index in list(value["progress"]):
            with database.locked(config), database.connect(config.state_root) as db:
                value, _ = journal.load(db, identity)
                progress = value["progress"][index]
                if progress["state"] == "removed":
                    continue
                path = value["plan"]["files"][int(index)]["target_path"]
                if not (root / path).exists() and progress.get("removing"):
                    progress["state"] = "removed"
                    journal.save(db, identity, value, "rolling_back")
                    continue
                raw, key = publication_file_io.read(root, path)
                progress["removing"] = True
                journal.save(db, identity, value, "rolling_back")
                publication_file_io.remove_owned(root, path, key, digest(raw))
                progress["state"] = "removed"
                journal.save(db, identity, value, "rolling_back")
        with database.locked(config), database.connect(config.state_root) as db:
            value, _ = journal.load(db, identity)
            for path, key in sorted(
                value.get("directories", {}).items(),
                key=lambda pair: pair[0].count("/"),
                reverse=True,
            ):
                directory = root / path
                if not directory.exists():
                    continue
                if vault_directories.key(directory) != key:
                    raise IntegrityError("vault_merge_owned_directory_changed")
                if not any(directory.iterdir()):
                    from .file_exchange import parent_fd

                    parent = parent_fd(root, path)
                    try:
                        info = os.stat(directory.name, dir_fd=parent, follow_symlinks=False)
                        if [info.st_dev, info.st_ino] != key:
                            raise IntegrityError("vault_merge_owned_directory_changed")
                        os.rmdir(directory.name, dir_fd=parent)
                        os.fsync(parent)
                    finally:
                        os.close(parent)
            value["phase"] = "rolled_back"
            value["reason"] = "vault_merge_rolled_back_originals_and_audits_retained"
            journal.save(db, identity, value, "rolled_back")
        return journal_view(config, identity)


def journal_view(config: Config, identity: str) -> dict[str, Any]:
    with database.connect(config.state_root) as db:
        return journal.view(db, identity)
