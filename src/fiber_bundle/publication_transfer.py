"""Published-note migration with retained original-archive permissions and provenance."""

import hashlib
import os
import sqlite3
from dataclasses import asdict
from typing import Any

from . import (
    database,
    file_exchange,
    note_identity_store,
    note_inventory,
    note_migration,
    note_transfer_execution,
    note_transfer_stage,
)
from . import note_migration_plans as plans
from . import note_migration_store as journal
from . import note_transfer_links as links
from . import note_transfer_mocs as mocs
from . import publication_transfer_intent as intent
from . import publication_transfer_stage as stage
from .config import Config, IntegrityError


def prepare(
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
    target_moc: str | None,
    moc_revision: int | None,
    request: str | None,
) -> tuple[str, dict[str, Any], str]:
    args = (
        config,
        db,
        client,
        source,
        target,
        identity,
        revision,
        source_path,
        target_path,
        expected,
    )
    plan, files, unresolved = intent.observe(*args)
    again, again_files, again_links = intent.observe(*args)
    if (plan, files, unresolved) != (again, again_files, again_links):
        raise IntegrityError("publication transfer evidence changed")
    before = next(
        (r for r in note_identity_store.records(config, source) if r.identity == identity), None
    )
    if before is None or plans.digest(asdict(before)) != plan["registration_sha256"]:
        raise IntegrityError("publication transfer registration changed")
    plan_id = plans.digest(plan)
    value: dict[str, Any] = {
        "schema": 4,
        "action": "transfer_publication",
        "plan_id": plan_id,
        "plan": plan,
        "registration": asdict(before),
        "request_id": request or plan_id,
        "transfer": {
            "moc_changes": mocs.freeze(config, plan, target_moc, moc_revision),
            "unresolved_note_links": unresolved,
            "incoming_note_links": links.inbound(
                note_inventory.read(
                    config, db, note_migration.endpoints(config, db, client, source, target)[0]
                ),
                plan["source_path"],
            ),
        },
    }
    intent.validate(value)
    execution = plans.digest(value)
    intent.save_files(config, execution, files)
    current, current_files, current_links = intent.observe(*args)
    if (current, current_files, current_links) != (plan, files, unresolved):
        raise IntegrityError("publication transfer changed before intent save")
    mocs.check(
        config,
        plan,
        value["transfer"]["moc_changes"],
        note_transfer_execution.roots(config, db, client, plan),
    )
    journal.save_value(config, value)
    value, phase = journal.load(config, client, source, target, execution)
    note_transfer_execution.roots(config, db, client, plan)
    return execution, value, phase


def recheck(
    config: Config, db: sqlite3.Connection, client: str, execution: str, value: dict[str, Any]
) -> None:
    plan = value["plan"]
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
    current, files, unresolved = intent.observe(*args)
    if (
        current != plan
        or unresolved != value["transfer"]["unresolved_note_links"]
        or files != intent.files(config, execution, value)
        or intent.observe(*args)[0] != plan
    ):
        raise IntegrityError("publication transfer current evidence changed")


def location(plan: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = dict(plan["publication"])
    result.update(
        vault_id=plan["target_vault_id"],
        relative_path=plan["target_path"].rsplit("/", 1)[0],
        vault_dev=plan["target_vault_key"][0],
        vault_ino=plan["target_vault_key"][1],
    )
    return result


def check_row(db: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
    row = db.execute(
        "SELECT * FROM article_publications WHERE id=?", (plan["publication"]["id"],)
    ).fetchone()
    if row is None or dict(row) not in (plan["publication"], location(plan)):
        raise IntegrityError("publication transfer ledger changed")
    original = db.execute(
        "SELECT s.vault_id FROM source_versions v JOIN sources s ON s.id=v.source_id WHERE v.id=?",
        (row["version_id"],),
    ).fetchone()
    if original is None or original[0] != plan["original_vault_id"]:
        raise IntegrityError("publication original archive scope changed")
    return dict(row)


def relocate(db: sqlite3.Connection, plan: dict[str, Any]) -> None:
    with database.transaction(db):
        if check_row(db, plan) == location(plan):
            return
        db.execute(
            "UPDATE article_publications SET vault_id=?,relative_path=?,vault_dev=?,vault_ino=? "
            "WHERE id=?",
            (
                plan["target_vault_id"],
                plan["target_path"].rsplit("/", 1)[0],
                *plan["target_vault_key"],
                plan["publication"]["id"],
            ),
        )
        if check_row(db, plan) != location(plan):
            raise IntegrityError("publication location transaction failed")


def finish(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    execution: str,
    value: dict[str, Any],
    action: str,
) -> tuple[str, bool, bool]:
    plan = value["plan"]
    roots = note_transfer_execution.roots(config, db, client, plan)
    bundle = plan["target_path"].rsplit("/", 1)[0]
    fds = []
    wrote = False
    try:
        fds.append(file_exchange.parent_fd(roots[0], plan["source_path"]))
        fds.append(file_exchange.parent_fd(roots[1], bundle))
        parent = os.fstat(fds[1])
        if [parent.st_dev, parent.st_ino] != plan["target_parent_key"]:
            raise IntegrityError("publication transfer target parent changed")

        def guard() -> None:
            current = note_transfer_execution.roots(config, db, client, plan)
            for root, path, old in zip(current, (plan["source_path"], bundle), fds, strict=True):
                fd = file_exchange.parent_fd(root, path)
                try:
                    a, b = os.fstat(fd), os.fstat(old)
                    if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
                        raise IntegrityError("publication transfer parent replaced")
                finally:
                    os.close(fd)
            check_row(db, plan)
            mocs.check(config, plan, value["transfer"]["moc_changes"], current)
            for item in plan["bundle_files"]:
                if item["source_path"] == plan["source_path"]:
                    continue
                data, key = links.read(current[0], item["source_path"])
                if key != item["file_key"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                    raise IntegrityError("publication transfer source attachment or marker changed")

        def write_guard() -> None:
            nonlocal wrote
            guard()
            wrote = True

        guard()
        intent.files(config, execution, value)
        physical = stage.load(config, execution, value)
        if physical is None:
            if action != "apply":
                if action == "reconcile" and note_transfer_stage.matches(
                    fds[0],
                    plan["source_path"].rsplit("/", 1)[-1],
                    plan["source_file_key"],
                    plan["source_sha256"],
                ):
                    try:
                        os.stat(bundle.rsplit("/", 1)[-1], dir_fd=fds[1], follow_symlinks=False)
                    except FileNotFoundError:
                        journal.transition(config, execution, "claimed", "aborted")
                        return "aborted", False, False
                raise IntegrityError("publication resume requires owned staging evidence")
            physical = stage.save(config, execution, value, (roots[1] / bundle).parent)
            wrote = True
        if not stage.operate(
            execution,
            value,
            physical,
            fds[0],
            fds[1],
            write=action in {"apply", "resume"},
            guard=write_guard,
        ):
            return "claimed", False, False
        guard()
        relocate(db, plan)
        guard()
        basename = plan["target_path"].rsplit("/", 1)[-1]
        note_identity_store.transfer(
            config,
            plan["source_vault_id"],
            plan["target_vault_id"],
            client,
            journal.registration(value),
            plan["target_path"],
            tuple(plan["target_vault_key"]),
            execution,
            sha256=plan["source_sha256"],
            file_key=tuple(physical["files"][basename]),
        )
        guard()
        mocs.apply(config, client, execution, value["transfer"]["moc_changes"], guard=guard)
        guard()
        if not stage.operate(execution, value, physical, fds[0], fds[1], write=False, guard=guard):
            raise IntegrityError(
                "publication transfer physical state changed after location commit"
            )
        journal.transition(config, execution, "claimed", "completed")
        return "completed", wrote, True
    finally:
        for fd in fds:
            os.close(fd)
