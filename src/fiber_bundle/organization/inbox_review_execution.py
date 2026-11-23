"""Durable exclusive moves and CAS exchanges, with explicit crash reconciliation."""

import base64
import os
from contextlib import contextmanager
from typing import Any

from .. import file_exchange, note_placement, vault_directories
from ..config import IntegrityError
from . import inbox_review as review
from . import inbox_review_ledger as ledger
from . import inbox_review_plan as plans


@contextmanager
def stage(root: Any, value: Any) -> Any:
    name = value.get("stage_name", "")
    if not name.startswith(".fiber-inbox-review-" + value["id"] + "-") or "/" in name:
        raise IntegrityError("inbox_review_stage_invalid")
    fd = file_exchange.parent_fd(root, name + "/child")
    try:
        if review.directory(os.fstat(fd)) != value["stage_key"]:
            raise IntegrityError("inbox_review_stage_changed")
        yield fd
        if review.directory((root / name).lstat()) != value["stage_key"]:
            raise IntegrityError("inbox_review_stage_changed")
    finally:
        os.close(fd)


def create_stage(config: Any, root: Any, value: Any) -> None:
    if value.get("stage_key"):
        return
    root_fd = file_exchange.parent_fd(root, "child")
    try:
        if review.directory(os.fstat(root_fd)) != value["fixed"]["root"]:
            raise IntegrityError("inbox_review_scope_or_directory_changed")
        # An unclaimed directory can only precede every file mutation. Preserve it,
        # then claim another exclusive directory; never adopt unknown recovery files.
        for attempt in range(value.get("stage_attempt", 0) + 1, 10):
            name = f".fiber-inbox-review-{value['id']}-{attempt}"
            value.update(stage_name=name, stage_attempt=attempt)
            plans.save(config, value, "applying")
            try:
                os.mkdir(name, 0o700, dir_fd=root_fd)
            except FileExistsError:
                continue
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            try:
                value["stage_key"] = review.directory(os.fstat(fd))
                os.fsync(fd)
            finally:
                os.close(fd)
            os.fsync(root_fd)
            plans.save(config, value, "applying")
            return
        raise IntegrityError("inbox_review_stage_conflict")
    finally:
        os.close(root_fd)


def check_restorable(root: Any, original: Any) -> Any:
    if original["action"] == "quarantine_empty":
        path = root / original["path"]
        if path.exists() or path.is_symlink():
            raise IntegrityError("inbox_review_restore_path_occupied")
        with stage(root, original):
            _, evidence = review.read(root, original["stage_name"] + "/quarantine.md")
    else:
        with stage(root, original):
            backup, backup_evidence = review.read(
                root, original["stage_name"] + "/" + original["payload_name"]
            )
            if backup_evidence != original.get(
                "restore_source_evidence", original["evidence"]
            ) or backup != base64.b64decode(
                original.get("restore_source", original["before"]), validate=True
            ):
                raise IntegrityError("inbox_review_backup_changed")
        _, evidence = review.read(root, original["path"])
    if evidence != original["result_evidence"]:
        raise IntegrityError("inbox_review_restore_file_changed")
    return evidence


def _move(config: Any, root: Any, allowed: Any, value: Any, original: Any = None) -> None:
    restoring = original is not None
    owner = original if restoring else value
    if not restoring:
        create_stage(config, root, value)
    hidden = owner["stage_name"] + "/quarantine.md"
    source, target = (hidden, value["path"]) if restoring else (value["path"], hidden)
    source_exists = (root / source).exists() or (root / source).is_symlink()
    target_exists = (root / target).exists() or (root / target).is_symlink()
    if source_exists == target_exists:
        raise IntegrityError("inbox_review_move_conflict")
    current = source if source_exists else target
    with stage(root, owner) as stage_fd:
        _, evidence = review.read(root, current)
        if evidence != value["evidence"]:
            raise IntegrityError("inbox_review_file_changed")
        with vault_directories.opened_parent(root, allowed, value["path"].rpartition("/")[0]) as (
            _,
            parent_fd,
        ):
            if review.directory(os.fstat(parent_fd)) != value["fixed"]["parent"]:
                raise IntegrityError("inbox_review_scope_or_directory_changed")
            if source_exists:
                source_fd, target_fd = (stage_fd, parent_fd) if restoring else (parent_fd, stage_fd)
                source_name, target_name = source.rsplit("/", 1)[-1], target.rsplit("/", 1)[-1]
                again, key = file_exchange.read(source_fd, source_name)
                if (
                    digest_bytes(again) != value["evidence"]["sha256"]
                    or list(key) != value["evidence"]["key"][:2]
                ):
                    raise IntegrityError("inbox_review_file_changed")
                file_exchange.rename(source_fd, source_name, target_fd, target_name, exchange=False)
            _, result = review.read(root, target)
            if result != value["evidence"]:
                if not restoring:
                    # Preserve the external edit and expose only an explicit no-replace
                    # restore. Never silently accept it as a disposable empty file.
                    value.update(result_evidence=result, restore_only=True)
                    plans.save(config, value, "reconcile")
                raise IntegrityError("inbox_review_concurrent_edit_preserved")
            value["result_evidence"] = result
            value["file_done"] = True
            plans.save(config, value, "applying")


def digest_bytes(raw: bytes) -> str:
    from ..objects import digest

    return digest(raw)


def _private_bytes(fd: int, name: str) -> tuple[bytes, list[int]]:
    """An interrupted owned UTF-8 write may end inside a multibyte character."""
    import stat

    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(handle, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > review.MAX_BYTES:
            raise IntegrityError("inbox_review_stage_changed")
        raw = stream.read(review.MAX_BYTES + 1)
        after = os.fstat(stream.fileno())
        if (
            info.st_size != len(raw)
            or info.st_mtime_ns != after.st_mtime_ns
            or info.st_ctime_ns != after.st_ctime_ns
        ):
            raise IntegrityError("inbox_review_stage_changed")
        return raw, [info.st_dev, info.st_ino]


def _payload(config: Any, root: Any, value: Any, fd: int) -> str:
    raw = base64.b64decode(value["after"], validate=True)
    if not value.get("payload_key"):
        for attempt in range(value.get("payload_attempt", 0) + 1, 10):
            name = f"payload-{attempt}.md"
            value.update(payload_name=name, payload_attempt=attempt)
            plans.save(config, value, "applying")
            try:
                handle = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    value["evidence"]["mode"],
                    dir_fd=fd,
                )
            except FileExistsError:
                continue
            try:
                os.fchmod(handle, value["evidence"]["mode"])
                info = os.fstat(handle)
                value["payload_key"] = [info.st_dev, info.st_ino]
                plans.save(config, value, "applying")
                with os.fdopen(handle, "wb", closefd=False) as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(handle)
            finally:
                os.close(handle)
            os.fsync(fd)
            value["payload_ready"] = True
            plans.save(config, value, "applying")
            break
        else:
            raise IntegrityError("inbox_review_stage_conflict")
    name = value["payload_name"]
    if not value.get("payload_ready"):
        # Only our claimed independent inode may finish an interrupted staged write.
        staged, key = _private_bytes(fd, name)
        if key != value["payload_key"] or not raw.startswith(staged):
            raise IntegrityError("inbox_review_stage_changed")
        handle = os.open(name, os.O_WRONLY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            if [os.fstat(handle).st_dev, os.fstat(handle).st_ino] != value["payload_key"]:
                raise IntegrityError("inbox_review_stage_changed")
            with os.fdopen(handle, "wb", closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(handle)
        finally:
            os.close(handle)
        value["payload_ready"] = True
        plans.save(config, value, "applying")
    return str(name)


def _exchange(config: Any, db: Any, root: Any, allowed: Any, value: Any) -> None:
    create_stage(config, root, value)
    with stage(root, value) as stage_fd:
        name = _payload(config, root, value, stage_fd)
        hidden = value["stage_name"] + "/" + name
        current, evidence = review.read(root, value["path"])
        backup, backup_evidence = review.read(root, hidden)
        before = base64.b64decode(value["before"], validate=True)
        after = base64.b64decode(value["after"], validate=True)
        if evidence["key"][:2] == value["payload_key"] and current == after:
            if backup != before or backup_evidence != value["evidence"]:
                value.update(
                    restore_only=True,
                    result_evidence=evidence,
                    restore_source_evidence=backup_evidence,
                    restore_source=base64.b64encode(backup).decode(),
                )
                plans.save(config, value, "reconcile")
                raise IntegrityError("inbox_review_concurrent_edit_preserved")
        else:
            if (
                current != before
                or evidence != value["evidence"]
                or backup != after
                or backup_evidence["key"][:2] != value["payload_key"]
            ):
                raise IntegrityError("inbox_review_file_changed")
            with vault_directories.opened_parent(
                root, allowed, value["path"].rpartition("/")[0]
            ) as (_, parent_fd):
                if review.directory(os.fstat(parent_fd)) != value["fixed"]["parent"]:
                    raise IntegrityError("inbox_review_scope_or_directory_changed")
                again, key = file_exchange.read(parent_fd, value["path"].rsplit("/", 1)[-1])
                if again != before or list(key) != evidence["key"][:2]:
                    raise IntegrityError("inbox_review_file_changed")
                if value["action"] == "unlink_missing":
                    job = review.task(
                        config,
                        db,
                        value["client_id"],
                        value["vault_id"],
                        value["job_id"],
                        value["path"],
                    )
                    links = review.repairable(root, allowed, job, value["path"], before)
                    if not set(value["link_ids"]) <= {link["id"] for link in links}:
                        raise IntegrityError("inbox_review_link_selection_changed")
                file_exchange.rename(
                    stage_fd, name, parent_fd, value["path"].rsplit("/", 1)[-1], exchange=True
                )
            backup, backup_evidence = review.read(root, hidden)
            if backup != before or backup_evidence != value["evidence"]:
                current, result = review.read(root, value["path"])
                if current == after and result["key"][:2] == value["payload_key"]:
                    value.update(
                        restore_only=True,
                        result_evidence=result,
                        restore_source_evidence=backup_evidence,
                        restore_source=base64.b64encode(backup).decode(),
                    )
                    plans.save(config, value, "reconcile")
                raise IntegrityError("inbox_review_concurrent_edit_preserved")
        current, evidence = review.read(root, value["path"])
        if current != after or evidence["key"][:2] != value["payload_key"]:
            raise IntegrityError("inbox_review_file_changed")
        value.update(result_evidence=evidence, file_done=True)
        plans.save(config, value, "applying")


def restore_chain(config: Any, db: Any, value: Any) -> list[Any]:
    chain: list[Any] = []
    seen = {value["id"]}
    target = value.get("restores")
    while target:
        if target in seen or len(chain) >= 16:
            raise IntegrityError("inbox_review_restore_chain_invalid")
        ancestor = plans.load(config, db, value["client_id"], value["vault_id"], target)
        if (
            ancestor["path"] != value["path"]
            or ancestor["fixed"] != value["fixed"]
            or plans.seal(ancestor) != ancestor["plan_sha256"]
            or ancestor["state"] != "completed"
            and not ancestor.get("restore_only")
        ):
            raise IntegrityError("inbox_review_restore_chain_invalid")
        chain.append(ancestor)
        seen.add(target)
        target = ancestor.get("restores")
    return chain


def apply(config: Any, db: Any, client: str, vault: str, identity: str, expected: str) -> Any:
    value = plans.load(config, db, client, vault, identity)
    if plans.seal(value) != expected or value["plan_sha256"] != expected:
        raise IntegrityError("inbox_review_plan_changed")
    root, allowed, fixed = review.context(config, db, client, vault, value["path"])
    if fixed != value["fixed"]:
        raise IntegrityError("inbox_review_scope_or_directory_changed")
    if value["state"] == "completed":
        return plans.public(value)
    if value.get("restore_only"):
        raise IntegrityError("inbox_review_concurrent_edit_restore_required")
    note_placement.guard(db, vault)
    ancestors = restore_chain(config, db, value)
    if ledger.pending(config, vault, value["path"], {identity, *(row["id"] for row in ancestors)}):
        raise IntegrityError("inbox_review_reconciliation_required")
    original = ancestors[0] if ancestors else None
    if value["state"] == "prepared":
        if original:
            if original.get("restored_by"):
                raise IntegrityError("inbox_review_already_restored")
            check_restorable(root, original)
        else:
            job = review.task(config, db, client, vault, value["job_id"], value["path"])
            raw, evidence = review.read(root, value["path"])
            if evidence != value["evidence"]:
                raise IntegrityError("inbox_review_file_changed")
            if value["action"] == "quarantine_empty":
                if review.classification(raw) not in {"empty", "title_only"}:
                    raise IntegrityError("inbox_review_not_empty")
            elif not set(value["link_ids"]) <= {
                link["id"] for link in review.repairable(root, allowed, job, value["path"], raw)
            }:
                raise IntegrityError("inbox_review_link_selection_changed")
        from .inbox_review_metadata import check

        check(config, db, value)
    plans.save(config, value, "applying")
    try:
        if "quarantine" in value["action"]:
            _move(config, root, allowed, value, original)
        else:
            _exchange(config, db, root, allowed, value)
            from .inbox_review_metadata import commit

            commit(config, db, value)
        from .. import note_search_jobs, placement_sync

        placement_sync.committed(
            config,
            db,
            identity,
            {
                "metadata_committed": True,
                "root_key": fixed["root"][:2],
                "client_id": client,
                "vault_id": vault,
                "moves": {},
                "updates": [{"path": value["path"], "target": value["path"]}],
            },
            root,
        )
        note_search_jobs.changed(config, vault)
        for ancestor in ancestors:
            ancestor["restored_by"] = identity
            plans.save(config, ancestor, "completed")
        value.pop("error", None)
        plans.save(config, value, "completed")
    except BaseException as error:
        value["error"] = str(error)
        plans.save(config, value, "reconcile")
        raise
    return plans.public(value)
