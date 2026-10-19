"""Stream verification outside the shared storage lease before binding or visibility cutover."""

import hashlib
import os
import stat
from pathlib import Path
from typing import Any

from . import database, file_exchange
from . import vault_merge_journal as journal
from .config import Config, IntegrityError


def file(root: Path, path: str, sha: str, size: int, key: list[Any]) -> None:
    parent = file_exchange.parent_fd(root, path)
    try:
        fd = os.open(Path(path).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or [before.st_dev, before.st_ino] != key[:2]
            or before.st_size != size
        ):
            raise IntegrityError("vault_merge_verified_file_identity_changed")
        digest = hashlib.sha256()
        count = 0
        while chunk := stream.read(1024 * 1024):
            count += len(chunk)
            if count > size:
                raise IntegrityError("vault_merge_verified_file_size_changed")
            digest.update(chunk)
        after = os.fstat(stream.fileno())
        if (
            count != size
            or digest.hexdigest() != sha
            or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        ):
            raise IntegrityError("vault_merge_verified_file_bytes_changed")


def current(config: Config, identity: str) -> None:
    from .vault_merge_execution import authorize
    from .vault_merge_recovery import roots

    with database.locked(config), database.connect(config.state_root) as db:
        value, _ = journal.load(db, identity)
        authorize(config, db, identity, value)
        root = roots(config, db, value)
    for inventory in value["plan"]["inventories"]:
        base = Path(inventory["context"]["root"])
        for item in inventory["files"]:
            file(base, item["path"], item["sha256"], item["bytes"], [item["dev"], item["ino"]])
    for index, progress in value["progress"].items():
        item = value["plan"]["files"][int(index)]
        if progress["state"] != "copied":
            raise IntegrityError("vault_merge_copy_not_complete")
        file(
            root, item["target_path"], progress["sha256"], item["projected_bytes"], progress["key"]
        )
    # Scope/epoch changes during streaming invalidate the stage too.
    with database.locked(config), database.connect(config.state_root) as db:
        authorize(config, db, identity, value)
