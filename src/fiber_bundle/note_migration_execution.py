"""Exclusive same-filesystem historical-note move with explicit crash reconciliation."""

import hashlib
import os
import sqlite3
from typing import Any

from . import (
    auth,
    database,
    file_exchange,
    moc_projection,
    note_identity_store,
    note_inventory,
    note_migration,
    note_transfer_execution,
    publication_transfer,
    vaults,
)
from . import (
    note_migration_plans as plans,
)
from . import (
    note_migration_store as journal,
)
from .config import Config, IntegrityError, InvalidInput


def current(config: Config, db: Any, client: str, value: dict[str, Any]) -> None:
    args = (
        config,
        db,
        client,
        value["source_vault_id"],
        value["target_vault_id"],
        value["note_identity"],
        value["revision"],
        value["source_path"],
        value["target_path"],
        value["source_sha256"],
    )
    if note_migration.observe(*args) != value or note_migration.observe(*args) != value:
        raise IntegrityError("migration execution evidence changed")
    if value["review_flags"]:
        raise InvalidInput("migration dependencies require a separate reviewed execution scope")


def physical(fd: int, name: str, plan: dict[str, Any]) -> bool:
    try:
        data, key = file_exchange.read(fd, name)
    except FileNotFoundError:
        return False
    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    birth = getattr(info, "st_birthtime", None)
    full_key = [*key, float(birth if birth is not None else 0).hex()]
    if (
        full_key != plan["source_file_key"]
        or hashlib.sha256(data).hexdigest() != plan["source_sha256"]
    ):
        raise IntegrityError("migration physical file changed; preserve both locations")
    return True


def finish(
    config: Config,
    db: Any,
    client: str,
    execution: str,
    plan: dict[str, Any],
    *,
    move: bool,
    before: note_identity_store.Registration,
) -> str:
    source, target = plan["source_vault_id"], plan["target_vault_id"]
    rows = note_migration.endpoints(config, db, client, source, target)
    roots = [vaults.checked_vault(config, row) for row in rows]
    for root, field in zip(roots, ("source_vault_key", "target_vault_key"), strict=True):
        info = root.stat()
        if [info.st_dev, info.st_ino] != plan[field]:
            raise IntegrityError("migration Vault changed")
    fds = []
    try:
        for root, path in zip(roots, (plan["source_path"], plan["target_path"]), strict=True):
            fds.append(file_exchange.parent_fd(root, path))
        parent = os.fstat(fds[1])
        if [parent.st_dev, parent.st_ino] != plan["target_parent_key"]:
            raise IntegrityError("migration target parent changed")

        def guard() -> None:
            current_rows = note_migration.endpoints(config, db, client, source, target)
            for row, field, path, old_fd in zip(
                current_rows,
                ("source_vault_key", "target_vault_key"),
                (plan["source_path"], plan["target_path"]),
                fds,
                strict=True,
            ):
                root = vaults.checked_vault(config, row)
                info = root.stat()
                if [info.st_dev, info.st_ino] != plan[field]:
                    raise IntegrityError("migration Vault replaced")
                fd = file_exchange.parent_fd(root, path)
                try:
                    a, b = os.fstat(fd), os.fstat(old_fd)
                    if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
                        raise IntegrityError("migration parent replaced")
                finally:
                    os.close(fd)

        guard()
        names = [plan[k].rsplit("/", 1)[-1] for k in ("source_path", "target_path")]
        src, dst = [physical(fd, name, plan) for fd, name in zip(fds, names, strict=True)]
        # Reconciliation never retries a file move. An unexecuted claim is closed as aborted.
        if not move and src and not dst:
            journal.transition(config, execution, "claimed", "aborted")
            return "aborted"
        if move:
            if not src or dst:
                raise IntegrityError("migration locations conflict")
            guard()
            file_exchange.rename(fds[0], names[0], fds[1], names[1], exchange=False)
        if physical(fds[0], names[0], plan) or not physical(fds[1], names[1], plan):
            raise IntegrityError("migration file move is unresolved")
        guard()
        # Do not silently accept new MOC ownership during a partially completed migration.
        for root, vid, field in zip(
            roots, (source, target), ("source_mocs_sha256", "target_mocs_sha256"), strict=True
        ):
            empty = note_inventory.Inventory(root, [], {}, {}, {})
            if plans.digest(note_migration.associations(config, vid, empty)) != plan[field]:
                raise IntegrityError("migration MOC ownership changed")
        guard()
        note_identity_store.transfer(
            config,
            source,
            target,
            client,
            before,
            plan["target_path"],
            tuple(plan["target_vault_key"]),
            execution,
            sha256=plan["source_sha256"],
        )
        if physical(fds[0], names[0], plan) or not physical(fds[1], names[1], plan):
            raise IntegrityError("migration evidence changed after identity transfer")
        guard()
        journal.transition(config, execution, "claimed", "completed")
        return "completed"
    finally:
        for fd in fds:
            os.close(fd)


def run(
    config: Config,
    authorization: str,
    source: str,
    target: str,
    action: str,
    *,
    plan_id: str | None = None,
    execution_id: str | None = None,
    target_moc_id: str | None = None,
    moc_revision: int | None = None,
    request_id: str | None = None,
    note_identity: str | None = None,
    revision: int | None = None,
    source_path: str | None = None,
    target_path: str | None = None,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            client = auth.client(db, authorization)["id"]
            note_migration.endpoints(config, db, client, source, target)
            wrote = False
            verified = False
            value = None
            if action not in {"prepare-reviewed", "prepare-publication"} and any(
                v is not None for v in (target_moc_id, moc_revision, request_id)
            ):
                raise InvalidInput("only reviewed preparation accepts dependency selections")
            raw = (note_identity, revision, source_path, target_path, expected_sha256)
            if action != "prepare-publication" and any(v is not None for v in raw):
                raise InvalidInput("execution requires a fixed intent")
            if action == "prepare-publication" and plan_id is None and execution_id is None:
                if any(v is None for v in raw):
                    raise InvalidInput("publication preparation requires complete source evidence")
                assert note_identity is not None and revision is not None
                assert source_path is not None and target_path is not None
                assert expected_sha256 is not None
                execution_id, value, phase = publication_transfer.prepare(
                    config,
                    db,
                    client,
                    source,
                    target,
                    note_identity,
                    revision,
                    source_path,
                    target_path,
                    expected_sha256,
                    target_moc_id,
                    moc_revision,
                    request_id,
                )
                plan = value["plan"]
            elif action == "prepare" and plan_id is not None and execution_id is None:
                plan = plans.load(config, client, source, target, plan_id)
                current(config, db, client, plan)
                note_migration.endpoints(config, db, client, source, target)
                execution_id = journal.save(config, plan_id, plan)
                _, phase = journal.load(config, client, source, target, execution_id)
            elif action == "prepare-reviewed" and plan_id is not None and execution_id is None:
                execution_id, value, phase = note_transfer_execution.prepare(
                    config,
                    db,
                    client,
                    source,
                    target,
                    plan_id,
                    target_moc_id,
                    moc_revision,
                    request_id,
                )
                plan = value["plan"]
            elif (
                action in {"apply", "reconcile", "status", "resume", "project-mocs"}
                and execution_id is not None
                and plan_id is None
            ):
                value, phase = journal.load(config, client, source, target, execution_id)
                plan = value["plan"]
                if action == "project-mocs" and (phase != "completed" or value["schema"] == 2):
                    raise InvalidInput("MOC projection requires a completed reviewed migration")
                if action == "apply":
                    if phase == "prepared":
                        if value["schema"] == 4:
                            publication_transfer.recheck(config, db, client, execution_id, value)
                        elif value["schema"] == 3:
                            note_transfer_execution.observe(config, db, client, plan)
                            from . import note_transfer_intent

                            note_transfer_intent.recheck(
                                config,
                                execution_id,
                                value,
                                note_transfer_execution.roots(config, db, client, plan)[0],
                            )
                        else:
                            current(config, db, client, plan)
                        note_migration.endpoints(config, db, client, source, target)
                        journal.transition(config, execution_id, "prepared", "claimed")
                        if value["schema"] in {3, 4}:
                            handler = (
                                publication_transfer
                                if value["schema"] == 4
                                else note_transfer_execution
                            )
                            phase, wrote, verified = handler.finish(
                                config, db, client, execution_id, value, "apply"
                            )
                        else:
                            phase = finish(
                                config,
                                db,
                                client,
                                execution_id,
                                plan,
                                move=True,
                                before=journal.registration(value),
                            )
                            wrote = True
                            verified = True
                    elif phase != "completed":
                        raise IntegrityError(
                            "migration claim cannot be replayed; explicitly reconcile"
                        )
                elif action == "reconcile" and phase == "claimed":
                    if value["schema"] in {3, 4}:
                        handler = (
                            publication_transfer
                            if value["schema"] == 4
                            else note_transfer_execution
                        )
                        phase, wrote, verified = handler.finish(
                            config, db, client, execution_id, value, "reconcile"
                        )
                    else:
                        phase = finish(
                            config,
                            db,
                            client,
                            execution_id,
                            plan,
                            move=False,
                            before=journal.registration(value),
                        )
                        verified = phase == "completed"
                elif action == "resume":
                    if value["schema"] not in {3, 4} or phase != "claimed":
                        raise InvalidInput("resume requires a claimed reviewed transfer")
                    handler = (
                        publication_transfer if value["schema"] == 4 else note_transfer_execution
                    )
                    phase, wrote, verified = handler.finish(
                        config, db, client, execution_id, value, "resume"
                    )
            else:
                raise InvalidInput("invalid migration execution arguments")
            note_migration.endpoints(config, db, client, source, target)
            result = {
                "status": phase,
                "execution_id": execution_id,
                "note_identity": plan["note_identity"],
                "source_vault_id": source,
                "target_vault_id": target,
                "vault_write_performed": wrote,
                "current_file_verified": verified,
                "requires_reconciliation": phase == "claimed",
            }
            if value is not None and value["schema"] in {3, 4}:
                transfer = value["transfer"]
                result.update(
                    unresolved_note_links=transfer["unresolved_note_links"],
                    incoming_note_links=transfer.get("incoming_note_links", []),
                    attachment_policy="copy_only_keep_source",
                    attachment_count=(
                        len(transfer["attachments"])
                        if value["schema"] == 3
                        else len(plan["bundle_files"]) - 2
                    ),
                    moc_projection_required=[
                        {
                            "vault_id": c["vault_id"],
                            "moc_id": c["moc_id"],
                            "revision": c["after"]["revision"],
                        }
                        for c in transfer["moc_changes"]
                    ],
                )
                if value["schema"] == 4:
                    result.update(
                        original_vault_id=plan["original_vault_id"],
                        source_version_id=plan["publication"]["version_id"],
                    )
                if action == "project-mocs":
                    outcomes = []
                    for c in transfer["moc_changes"]:
                        try:
                            outcome = moc_projection.refresh(
                                config,
                                db,
                                client,
                                c["vault_id"],
                                c["moc_id"],
                                c["after"]["revision"],
                            )
                        except (IntegrityError, OSError, ValueError, sqlite3.Error):
                            outcome = {"status": "conflict_preserved"}
                        outcomes.append(
                            {"vault_id": c["vault_id"], "moc_id": c["moc_id"], **outcome}
                        )
                    result["moc_projections"] = outcomes
                    result["vault_write_performed"] = any(
                        o.get("moc_file_write_performed", False) for o in outcomes
                    )
            return result
        finally:
            db.close()
