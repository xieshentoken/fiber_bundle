"""Exclusive, bounded file copies outside the global lease; physical claims precede byte writes."""

import hashlib
import os
import stat
from pathlib import Path
from typing import Any

from . import (
    database,
    file_exchange,
    publication_file_io,
    publication_links,
    vault_directories,
)
from . import vault_merge_journal as journal
from .config import Config, IntegrityError, contained
from .objects import digest

CHUNK = 1024 * 1024


def projected(plan: dict[str, Any], item: dict[str, Any]) -> bytes | None:
    if (
        item.get("storage") == "object"
        or not item["source_path"].endswith(".md")
        or item["role"] == "book"
    ):
        return None
    source = next(
        s for s in plan["inventories"] if s["context"]["vault_id"] == item["source_vault_id"]
    )
    root = Path(source["context"]["root"])
    raw, _ = publication_file_io.read(root, item["source_path"])
    rewritten, unresolved = publication_links.relocate(
        raw.decode("utf-8-sig"),
        root,
        item["source_path"],
        item["target_path"],
        [f["path"] for f in source["files"]],
        {
            f["source_path"]: f["target_path"]
            for f in plan["files"]
            if f["source_vault_id"] == item["source_vault_id"]
        },
    )
    if unresolved or digest(rewritten.encode()) != item["projected_sha256"]:
        raise IntegrityError("vault_merge_link_projection_changed")
    return rewritten.encode()


def parents(
    db: Any, identity: str, value: dict[str, Any], root: Path, path: str, scope: Any
) -> int:
    current = ""
    for part in path.split("/")[:-1]:
        parent = current
        current = (current + "/" if current else "") + part
        with vault_directories.opened_parent(
            root, scope, parent, internal_root=internal(value, path)
        ) as (_, fd):
            child = contained(root, current)
            if not child.exists():
                os.mkdir(part, mode=0o700, dir_fd=fd)
                os.fsync(fd)
                value.setdefault("directories", {})[current] = vault_directories.key(child)
                journal.save(db, identity, value, value["phase"])
            elif (
                current in value.get("directories", {})
                and vault_directories.key(child) != value["directories"][current]
            ):
                raise IntegrityError("vault_merge_owned_directory_changed")
    return file_exchange.parent_fd(root, path)


def internal(value: dict[str, Any], path: str) -> str | None:
    parts = path.split("/")
    if ".fiber-expert-canonical" in parts:
        return "/".join(parts[: parts.index(".fiber-expert-canonical") + 1])
    return None


def copy(config: Config, identity: str, index: int) -> None:
    from .vault_merge_execution import authorize

    with database.locked(config), database.connect(config.state_root) as db:
        value, state = journal.load(db, identity)
        authorize(config, db, identity, value)
        item = value["plan"]["files"][index]
        progress = value["progress"].get(str(index))
        if progress and progress["state"] == "copied":
            return
        if progress:
            raise IntegrityError("vault_merge_copy_requires_reconciliation")
        root, scope, _ = vault_directories.vault_root(
            config, db, value["plan"]["selection"]["target_vault_id"]
        )
        parent = parents(db, identity, value, root, item["target_path"], scope)
        try:
            fd = os.open(
                Path(item["target_path"]).name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
            info = os.fstat(fd)
            os.fsync(parent)
            value["progress"][str(index)] = {"state": "writing", "key": [info.st_dev, info.st_ino]}
            journal.save(db, identity, value, state)
        finally:
            os.close(parent)
    try:
        payload = projected(value["plan"], item)
        source = next(
            s
            for s in value["plan"]["inventories"]
            if s["context"]["vault_id"] == item["source_vault_id"]
        )
        source_root = Path(source["context"]["root"])
        relative = item["source_path"]
        if item.get("storage") == "object":
            source_root, relative = config.state_root, item["source"]["path"]
        source_fd = file_exchange.parent_fd(source_root, relative)
        try:
            incoming = os.open(
                Path(relative).name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=source_fd,
            )
        finally:
            os.close(source_fd)
        sha = hashlib.sha256()
        count = 0
        with os.fdopen(incoming, "rb") as original, os.fdopen(fd, "wb", closefd=False) as output:
            before = os.fstat(original.fileno())
            frozen = item["source"]
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (frozen["dev"], frozen["ino"], frozen["bytes"], frozen["mtime_ns"])
            ):
                raise IntegrityError("vault_merge_source_file_changed")
            while chunk := original.read(CHUNK):
                count += len(chunk)
                sha.update(chunk)
                if count > frozen["bytes"]:
                    raise IntegrityError("vault_merge_source_size_changed")
                if payload is None:
                    output.write(chunk)
            if count != frozen["bytes"] or sha.hexdigest() != frozen["sha256"]:
                raise IntegrityError("vault_merge_source_hash_changed")
            if payload is not None:
                output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        with database.locked(config), database.connect(config.state_root) as db:
            current, state = journal.load(db, identity)
            authorize(config, db, identity, current)
            raw, key = publication_file_io.read(root, item["target_path"])
            if (
                key[:2] != current["progress"][str(index)]["key"]
                or digest(raw) != item["projected_sha256"]
            ):
                raise IntegrityError("vault_merge_target_file_changed")
            current["progress"][str(index)] = {"state": "copied", "key": key, "sha256": digest(raw)}
            journal.save(db, identity, current, state)
    finally:
        os.close(fd)
