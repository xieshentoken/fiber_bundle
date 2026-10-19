"""Reviewed transfer orchestration; resume only provably owned remaining local effects."""

import os
import sqlite3
from pathlib import Path
from typing import Any

from . import file_exchange, note_identity_store, note_inventory, note_migration, vaults
from . import note_migration_plans as plans
from . import note_migration_store as journal
from . import note_transfer_intent as intent
from . import note_transfer_links as links
from . import note_transfer_mocs as mocs
from . import note_transfer_stage as stage
from .config import Config, IntegrityError


def roots(config: Config, db: sqlite3.Connection, client: str, plan: dict[str, Any]) -> list[Path]:
    rows = note_migration.endpoints(
        config, db, client, plan["source_vault_id"], plan["target_vault_id"]
    )
    result = [vaults.checked_vault(config, row) for row in rows]
    for root, name in zip(result, ("source_vault_key", "target_vault_key"), strict=True):
        info = root.stat()
        if [info.st_dev, info.st_ino] != plan[name]:
            raise IntegrityError("transfer Vault identity changed")
    return result


def observe(config: Config, db: sqlite3.Connection, client: str, plan: dict[str, Any]) -> None:
    args = (
        config,
        db,
        client,
        plan["source_vault_id"],
        plan["target_vault_id"],
        plan["note_identity"],
        plan["revision"],
        plan["source_path"],
        plan["target_path"],
        plan["source_sha256"],
    )
    if note_migration.observe(*args) != plan or note_migration.observe(*args) != plan:
        raise IntegrityError("reviewed migration preflight changed")


def prepare(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    source: str,
    target: str,
    plan_id: str,
    target_moc: str | None,
    revision: int | None,
    request: str | None,
) -> tuple[str, dict[str, Any], str]:
    plan = plans.load(config, client, source, target, plan_id)
    observe(config, db, client, plan)
    selected = roots(config, db, client, plan)
    value, files = intent.create(config, plan_id, plan, selected[0], target_moc, revision, request)
    again, again_files = intent.create(
        config, plan_id, plan, selected[0], target_moc, revision, request
    )
    if value != again or files != again_files:
        raise IntegrityError("transfer attachment or body scope changed")
    rows = note_migration.endpoints(config, db, client, source, target)
    value["transfer"]["incoming_note_links"] = links.inbound(
        note_inventory.read(config, db, rows[0]), plan["source_path"]
    )
    intent.validate(value)
    execution = plans.digest(value)
    intent.save_files(config, execution, files)
    observe(config, db, client, plan)
    intent.check_attachments(selected[0], value)
    mocs.check(config, plan, value["transfer"]["moc_changes"], roots(config, db, client, plan))
    journal.save_value(config, value)
    value, phase = journal.load(config, client, source, target, execution)
    roots(config, db, client, plan)
    return execution, value, phase


def finish(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    execution: str,
    value: dict[str, Any],
    action: str,
) -> tuple[str, bool, bool]:
    plan, transfer = value["plan"], value["transfer"]
    selected = roots(config, db, client, plan)
    fds = []
    wrote = False
    try:
        for root, name in zip(selected, ("source_path", "target_path"), strict=True):
            fds.append(file_exchange.parent_fd(root, plan[name]))
        info = os.fstat(fds[1])
        if [info.st_dev, info.st_ino] != plan["target_parent_key"]:
            raise IntegrityError("transfer target parent changed")

        def guard() -> None:
            current = roots(config, db, client, plan)
            for root, name, old_fd in zip(
                current, ("source_path", "target_path"), fds, strict=True
            ):
                fd = file_exchange.parent_fd(root, plan[name])
                try:
                    a, b = os.fstat(fd), os.fstat(old_fd)
                    if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
                        raise IntegrityError("transfer opened parent was replaced")
                finally:
                    os.close(fd)
            mocs.check(config, plan, transfer["moc_changes"], current)
            intent.check_attachments(current[0], value)

        def write_guard() -> None:
            nonlocal wrote
            guard()
            wrote = True

        guard()
        intent.files(config, execution, value)
        physical = stage.load(config, execution, value)
        if physical is None:
            if action != "apply":
                if action == "reconcile":
                    original = stage.matches(
                        fds[0],
                        plan["source_path"].rsplit("/", 1)[-1],
                        plan["source_file_key"],
                        plan["source_sha256"],
                    )
                    if not original:
                        raise IntegrityError("unproven transfer file effects require manual review")
                    for name in (
                        plan["target_path"].rsplit("/", 1)[-1],
                        stage.names(execution, value)[2],
                    ):
                        try:
                            os.stat(name, dir_fd=fds[1], follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        raise IntegrityError("unproven transfer target effect")
                    journal.transition(config, execution, "claimed", "aborted")
                    return "aborted", False, False
                raise IntegrityError("resume requires a durable owned staging record")
            parent = selected[1] / plan["target_path"]
            physical = stage.save(config, execution, value, parent.parent)
            wrote = True
            guard()
        complete = stage.operate(
            execution,
            value,
            physical,
            fds[0],
            fds[1],
            write=action in {"apply", "resume"},
            guard=write_guard,
        )
        if not complete:
            return "claimed", False, False
        guard()
        before = journal.registration(value)
        note_identity_store.transfer(
            config,
            plan["source_vault_id"],
            plan["target_vault_id"],
            client,
            before,
            plan["target_path"],
            tuple(plan["target_vault_key"]),
            execution,
            sha256=transfer["candidate_sha256"],
            file_key=tuple(physical["files"]["candidate.md"]),
        )
        guard()
        mocs.apply(config, client, execution, transfer["moc_changes"], guard=guard)
        guard()
        if not stage.operate(execution, value, physical, fds[0], fds[1], write=False, guard=guard):
            raise IntegrityError("transfer final physical state changed")
        journal.transition(config, execution, "claimed", "completed")
        return "completed", wrote, True
    finally:
        for fd in fds:
            os.close(fd)
