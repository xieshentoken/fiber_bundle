"""Plan, apply and explicitly reconcile one guarded MOC file projection."""

import hashlib
import os
import sqlite3
from typing import Any
from uuid import uuid4

from . import (
    auth,
    database,
    file_exchange,
    moc_associations,
    moc_dispatch_source,
    moc_file_identity,
    moc_markdown,
    moc_store,
    note_changes,
    note_inventory,
    note_publications,
    vaults,
)
from . import (
    moc_file_journal as journal,
)
from .config import Config, IntegrityError, InvalidInput, StorageError, contained
from .inputs import canonical


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def observed(
    inventory: note_inventory.Inventory,
    relative: str,
    *,
    record: moc_store.Record | None = None,
    data: bytes = b"",
    moc_id: str = "",
    config: Config | None = None,
    vault: str = "",
) -> str:
    if record is not None:
        from .organization import store

        projections = (
            {r["entity"]: r for r in store.rows(config, "note_moc", vault)} if config else {}
        )
        return moc_dispatch_source.observed(inventory, relative, record, data, moc_id, projections)
    return digest(
        canonical(
            [
                (n.relative_path, n.sha256, n.identity, n.publication_id, n.file_key)
                for n in sorted(inventory.notes, key=lambda n: n.relative_path)
                if n.relative_path != relative
            ]
        ).encode()
    )


def context(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vid: str,
    mid: str,
    *,
    plan: dict[str, Any] | None = None,
    allow_after: bool = False,
    source: dict[str, Any] | None = None,
    recovering: bool = False,
) -> tuple[moc_store.Record, note_inventory.Inventory]:
    if (plan or {}).get("graph_writeback_id") is not None:
        from .knowledge_graph.writeback import authorize

        assert plan is not None
        authorize(config, client, vid, plan["graph_writeback_id"])
    source = source if source is not None else (plan or {}).get("dispatch_source")
    if source is not None:
        moc_dispatch_source.authorize(config, db, client, source, recovery=recovering)
    auth.grant(db, client, vid, "add")
    vault = auth.grant(db, client, vid, "query")
    root = vaults.checked_vault(config, vault)
    if plan is not None:
        vaults.require_scope(config, vault, plan["path"])
    record = moc_store.load(config, vid, mid)
    info = root.stat()
    if record is None or (record.vault_dev, record.vault_ino) != (info.st_dev, info.st_ino):
        raise IntegrityError("MOC metadata absent or Vault identity changed")
    projection = (
        moc_file_identity.projection(config, vid, root, plan, allow_after=allow_after)
        if plan is not None
        else None
    )
    inventory = note_inventory.read(config, db, vault, projection=projection)
    if plan is not None:
        moc_file_identity.check_target(inventory, plan, allow_after=allow_after)
    if source is not None:
        if not recovering and not any(
            n.publication_id == source["publication_id"] for n in inventory.notes
        ):
            raise IntegrityError("MOC dispatch publication note is not currently visible")
        moc_dispatch_source.authorize(config, db, client, source, recovery=recovering)
    return record, inventory


def target(inventory: note_inventory.Inventory, relative: str) -> tuple[bytes, list[int] | None]:
    root = inventory.root
    note_changes.validate(
        [{"identity": "path:" + relative, "note_path": relative, "sha256": "0" * 64}]
    )
    if not relative.lower().endswith(".md") or len(relative.split("/")) > 33:
        raise InvalidInput("invalid MOC file target")
    for index in range(1, len(relative.split("/"))):
        ancestor = "/".join(relative.split("/")[:index])
        marker = contained(root, ancestor + "/" + note_publications.MARKER)
        if marker.exists() or marker.is_symlink():
            raise IntegrityError("MOC file cannot be in a publication bundle")
    path = contained(root, relative)
    note = next((n for n in inventory.notes if n.relative_path == relative), None)
    if path.exists() and (note is None or note.publication_id is not None):
        raise IntegrityError("MOC target must be a visible historical Markdown file")
    fd = file_exchange.parent_fd(root, relative)
    try:
        if not path.exists():
            return b"", None
        data, key = file_exchange.read(fd, path.name)
        if note is None or digest(data) != note.sha256:
            raise IntegrityError("MOC target changed during observation")
        return data, list(key)
    finally:
        os.close(fd)


def plan_file(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vid: str,
    mid: str,
    revision: int,
    relative: str,
    expected: str,
    *,
    source: dict[str, Any] | None = None,
    graph_writeback_id: str | None = None,
    missing_block_repair: tuple[str, str] | None = None,
) -> dict[str, Any]:
    vaults.require_scope(config, auth.grant(db, client, vid, "add"), relative)
    record, inventory = context(config, db, client, vid, mid, source=source)
    if record.revision != revision:
        raise IntegrityError("MOC revision conflict")
    items = journal.plans(config, vid, mid)
    if any(p.status == "pending" for p in items):
        raise IntegrityError("MOC file has a pending write; reconcile first")
    prior = journal.latest(items)
    data, key = target(inventory, relative)
    identity = moc_file_identity.capture(config, vid, inventory, relative)
    actual = digest(data) if key is not None else "absent"
    if actual != expected:
        raise IntegrityError("MOC file hash conflict")
    _, old_block, _ = moc_markdown.split(data, mid)
    if prior is not None:
        if prior.value["path"] != relative:
            moc_file_identity.check_moved_target(inventory, relative, prior.value)
        if digest(old_block) != prior.value["managed_sha256"] and not (
            not old_block and missing_block_repair == (prior.plan_id, actual)
        ):
            raise IntegrityError("MOC managed block missing or human edited")
    elif old_block:
        raise IntegrityError("unowned MOC managed block cannot be adopted")
    body = (
        data
        if key is not None
        else ("---\ntype: moc\n---\n# " + moc_associations.escape(record.title) + "\n\n").encode()
    )
    candidate, block, preserved = moc_markdown.render(body, mid, relative, record, inventory)
    if candidate == data:
        return {"status": "unchanged", "plan_id": None, "moc_file_write_performed": False}
    parent = contained(inventory.root, relative).parent.stat()
    info = inventory.root.stat()
    value = {
        "schema": 1,
        "operation": str(uuid4()),
        "vault_id": vid,
        "moc_id": mid,
        "revision": revision,
        "path": relative,
        "before_sha256": actual,
        "after_sha256": digest(candidate),
        "file_key": key,
        "vault_key": [info.st_dev, info.st_ino],
        "parent_key": [parent.st_dev, parent.st_ino],
        "inventory_sha256": observed(
            inventory,
            relative,
            record=record if source is not None else None,
            data=data,
            moc_id=mid,
            config=config,
            vault=vid,
        ),
        "managed_sha256": digest(block),
        "base_id": prior.plan_id if prior else None,
        "manual_links_preserved": preserved,
    }
    if identity is not None:
        value.update(schema=2, identity=identity, after_file_key=None)
    if source is not None:
        value.update(schema=3, identity=identity, after_file_key=None, dispatch_source=source)
    if graph_writeback_id is not None:
        value.update(
            schema=4, identity=identity, after_file_key=None, graph_writeback_id=graph_writeback_id
        )
    again_record, again = context(config, db, client, vid, mid, source=source)
    again_data, again_key = target(again, relative)
    if (
        again_record,
        observed(
            again,
            relative,
            record=again_record if source is not None else None,
            data=again_data,
            moc_id=mid,
            config=config,
            vault=vid,
        ),
        again_data,
        again_key,
    ) != (
        record,
        value["inventory_sha256"],
        data,
        key,
    ):
        raise IntegrityError("MOC evidence changed before plan save")
    if moc_file_identity.capture(config, vid, again, relative) != identity:
        raise IntegrityError("MOC identity changed before plan save")
    plan = journal.create(config, value, candidate)
    return {
        "status": "planned",
        "plan_id": plan.plan_id,
        **value,
        "markdown": candidate.decode("utf-8-sig"),
        "moc_file_write_performed": False,
    }


def apply_file(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vid: str,
    mid: str,
    plan_id: str,
    *,
    reconcile: bool = False,
) -> dict[str, Any]:
    auth.grant(db, client, vid, "add")
    auth.grant(db, client, vid, "query")
    items = journal.plans(config, vid, mid)
    plan = next((p for p in items if p.plan_id == plan_id), None)
    if plan is None:
        raise InvalidInput("MOC write plan does not exist")
    value, relative = plan.value, plan.value["path"]
    base = next((p for p in items if p.plan_id == value["base_id"]), None)

    def check_location(current: note_inventory.Inventory, *, allow_after: bool = False) -> None:
        if base is not None and base.value["path"] != relative:
            moved = next((n for n in current.notes if n.relative_path == relative), None)
            is_after = (
                allow_after
                and moved is not None
                and value.get("after_file_key") is not None
                and moved.file_key == (moc_file_identity.file_key(value["after_file_key"]))
            )
            moc_file_identity.check_moved_target(
                current, relative, base.value, applied=value if is_after else None
            )

    record, inventory = context(
        config,
        db,
        client,
        vid,
        mid,
        plan=value,
        allow_after=plan.status in {"pending", "applied"},
        recovering=reconcile,
    )
    check_location(inventory, allow_after=plan.status in {"pending", "applied"})
    info = inventory.root.stat()
    if list((info.st_dev, info.st_ino)) != value["vault_key"]:
        raise IntegrityError("MOC plan Vault identity changed")
    dest = contained(inventory.root, relative)
    src_fd = file_exchange.parent_fd(plan.path, "candidate.md")
    try:
        dst_fd = file_exchange.parent_fd(inventory.root, relative)
    except BaseException:
        os.close(src_fd)
        raise
    try:
        parent = os.fstat(dst_fd)
        if [parent.st_dev, parent.st_ino] != value["parent_key"]:
            raise IntegrityError("MOC parent directory changed")

        def read_target() -> tuple[str, list[int] | None]:
            try:
                data, key = file_exchange.read(dst_fd, dest.name)
                return digest(data), list(key)
            except FileNotFoundError:
                return "absent", None

        def candidate_hash() -> str:
            try:
                return digest(file_exchange.read(src_fd, "candidate.md")[0])
            except FileNotFoundError:
                return "absent"

        def candidate_key() -> list[int] | None:
            try:
                return list(file_exchange.read(src_fd, "candidate.md")[1])
            except FileNotFoundError:
                return None

        actual, key = read_target()
        before, after = value["before_sha256"], value["after_sha256"]
        if plan.status == "applied":
            if actual != after or (
                moc_file_identity.transition(value) is not None
                and moc_file_identity.unchanged(config, vid, value)
            ):
                raise IntegrityError("completed MOC file has since changed")
            return {"status": "applied", "plan_id": plan_id, "moc_file_write_performed": False}
        if reconcile:
            if plan.status != "pending":
                raise IntegrityError("only a pending MOC write may be reconciled")
            saved = candidate_hash()
            outcome = (
                "applied"
                if actual == after
                and saved == before
                and candidate_key() == value["file_key"]
                and moc_file_identity.candidate_matches(plan.path, value, before=True)
                else (
                    "not-applied"
                    if actual == before
                    and key == value["file_key"]
                    and saved == after
                    and moc_file_identity.candidate_matches(plan.path, value, before=False)
                    else None
                )
            )
            if outcome is None:
                raise IntegrityError(
                    "MOC write state conflicts; preserve both files for manual review"
                )
            if outcome == "applied":
                if value["schema"] == 3:
                    moc_dispatch_source.authorize(
                        config, db, client, value["dispatch_source"], recovery=True
                    )
                moc_file_identity.complete(config, db, vid, client, inventory.root, value)
            journal.finish(plan, outcome)
            return {"status": outcome, "plan_id": plan_id, "moc_file_write_performed": False}
        if plan.status != "planned" or any(p.status == "pending" for p in items):
            raise IntegrityError("MOC write already claimed; no automatic replay")
        latest = journal.latest(items)
        if (latest.plan_id if latest else None) != value["base_id"] or record.revision != value[
            "revision"
        ]:
            raise IntegrityError("MOC write plan is stale")
        if (
            actual != before
            or key != value["file_key"]
            or candidate_hash() != after
            or not moc_file_identity.candidate_matches(plan.path, value, before=False)
        ):
            raise IntegrityError("MOC target or candidate changed")
        current_record, current = context(config, db, client, vid, mid, plan=value)
        check_location(current)
        if (
            current_record != record
            or observed(
                current,
                relative,
                record=current_record if value["schema"] == 3 else None,
                data=file_exchange.read(dst_fd, dest.name)[0] if before != "absent" else b"",
                moc_id=mid,
                config=config,
                vault=vid,
            )
            != value["inventory_sha256"]
        ):
            raise IntegrityError("MOC sources changed before write")
        journal.write(plan.path / "claim", b"claimed\n")
        swapped = False
        try:
            if before != "absent":
                mode = os.stat(dest.name, dir_fd=dst_fd, follow_symlinks=False).st_mode & 0o777
                fd = os.open("candidate.md", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=src_fd)
                try:
                    os.fchmod(fd, mode)
                    os.fsync(fd)
                finally:
                    os.close(fd)
            file_exchange.rename(
                src_fd, "candidate.md", dst_fd, dest.name, exchange=before != "absent"
            )
            swapped = True
            if (
                read_target()[0] != after
                or candidate_hash() != before
                or candidate_key() != value["file_key"]
                or not moc_file_identity.candidate_matches(plan.path, value, before=True)
            ):
                raise IntegrityError("MOC target changed at atomic exchange")
            check_record, checked = context(
                config, db, client, vid, mid, plan=value, allow_after=True
            )
            check_location(checked, allow_after=True)
            check_parent = dest.parent.stat()
            if (
                check_record != record
                or observed(
                    checked,
                    relative,
                    record=check_record if value["schema"] == 3 else None,
                    data=file_exchange.read(dst_fd, dest.name)[0],
                    moc_id=mid,
                    config=config,
                    vault=vid,
                )
                != value["inventory_sha256"]
                or ([check_parent.st_dev, check_parent.st_ino] != value["parent_key"])
            ):
                raise IntegrityError("MOC context changed at write")
            if value["schema"] == 3:
                moc_dispatch_source.authorize(config, db, client, value["dispatch_source"])
            moc_file_identity.complete(config, db, vid, client, inventory.root, value)
        except (StorageError, auth.Denied, OSError, ValueError, sqlite3.Error):
            # Once the identity transaction committed, preserve the new file and
            # pending claim; rolling back only the file would break its identity.
            if (
                swapped
                and read_target()[0] == after
                and moc_file_identity.unchanged(config, vid, value)
                and (value["schema"] == 1 or read_target()[1] == value["after_file_key"][:2])
            ):
                file_exchange.rename(
                    dst_fd, dest.name, src_fd, "candidate.md", exchange=before != "absent"
                )
                journal.finish(plan, "not-applied")
            raise
        # If recording the outcome fails, leave the exclusive claim for explicit reconciliation.
        journal.finish(plan, "applied")
        return {
            "status": "applied",
            "plan_id": plan_id,
            "path": relative,
            "backup_path": str(plan.path / "candidate.md") if before != "absent" else None,
            "moc_file_write_performed": True,
        }
    finally:
        os.close(src_fd)
        os.close(dst_fd)


def run(
    config: Config,
    authorization: str,
    vault_id: str,
    action: str,
    moc_id: str,
    *,
    revision: int | None = None,
    relative: str | None = None,
    expected_sha256: str | None = None,
    plan_id: str | None = None,
) -> dict[str, Any]:
    note_changes.identifier(moc_id)
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            client = auth.client(db, authorization)["id"]
            if (
                action == "plan-file"
                and type(revision) is int
                and revision > 0
                and (relative is not None and expected_sha256 is not None and plan_id is None)
            ):
                return plan_file(
                    config, db, client, vault_id, moc_id, revision, relative, expected_sha256
                )
            if (
                action in {"apply-file", "reconcile-file"}
                and plan_id is not None
                and all(v is None for v in (revision, relative, expected_sha256))
                and note_changes.DIGEST.fullmatch(plan_id)
            ):
                return apply_file(
                    config,
                    db,
                    client,
                    vault_id,
                    moc_id,
                    plan_id,
                    reconcile=action == "reconcile-file",
                )
            raise InvalidInput("invalid MOC file action arguments")
        finally:
            db.close()
