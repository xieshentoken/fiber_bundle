"""Bounded local inventories and exclusive directory moves. No copy/delete fallback."""

import hashlib
import json
import os
import stat
import time
from pathlib import Path
from typing import Any, NoReturn

from .. import cloud_files, file_exchange, vault_catalog, vault_scope
from ..config import Config
from .errors import RelocationError

MAX_ENTRIES = 20000
MAX_BYTES = 2 * 1024**3
MAX_SECONDS = 60


def reject(code: str) -> NoReturn:
    raise RelocationError("relocation_" + code, 409)


def key(path: Path) -> list[int]:
    cloud_files.require_local(path)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or path.resolve(strict=True) != path:
        reject("directory_changed")
    return [info.st_dev, info.st_ino]


def local(path: Path) -> None:
    if any(
        p in {"Mobile Documents", "CloudStorage", "Dropbox", "OneDrive", "Google Drive"}
        or p.startswith("OneDrive - ")
        for p in path.parts
    ):
        reject("local_folder_required")
    cloud_files.require_local(path)
    from .filesystem import require_local

    require_local(path)


def target(
    config: Config, db: Any, row: Any, value: str, *, icloud: bool = False
) -> tuple[Path, Path]:
    source = vault_catalog.physical_path(config, row)
    path = Path(value)
    if not path.is_absolute() or len(value) > 4096 or "\0" in value or ".." in path.parts:
        reject("invalid_target")
    if path == Path(path.anchor) or path.name in {"", ".", ".."}:
        reject("invalid_target")
    if icloud:
        from .icloud import location, scopes

        scopes(source, path, row)
        location(source)
        location(path.parent)
    else:
        raw_scope = json.loads(row["scope_json"])
        before = vault_scope.effective(source, raw_scope)
        after = vault_scope.effective(path, raw_scope)
        if before.include != ("",) or before.exclude or after.include != ("",) or after.exclude:
            reject("full_scope_required")
        local(source)
        local(path.parent)
    if path.parent.resolve(strict=True) != path.parent:
        reject("invalid_target")
    if source == path or source in path.parents or path in source.parents:
        reject("overlap")
    state = config.state_root
    if path == state or path in state.parents or state in path.parents:
        reject("overlap")
    vault_catalog.overlaps(config, db, path, except_id=row["id"])
    if os.path.lexists(path):
        reject("target_exists")
    if key(source)[0] != key(path.parent)[0]:
        reject("same_disk_required")
    if not os.access(source.parent, os.W_OK | os.X_OK) or not os.access(
        path.parent, os.W_OK | os.X_OK
    ):
        reject("permission_required")
    return source, path


def inventory(
    root: Path, *, icloud: bool = False, excluded: tuple[str, ...] = ()
) -> list[dict[str, Any]]:
    """Read through no-follow directory descriptors; include hidden metadata and bytes."""
    if icloud:
        from .icloud import available

        available(root, excluded)
    else:
        local(root)
    root_key = key(root)
    found: list[dict[str, Any]] = []
    total = 0
    deadline = time.monotonic() + MAX_SECONDS

    def walk(parent_fd: int, parent: Path, depth: int) -> None:
        nonlocal total
        if depth > 48:
            reject("inventory_limit")
        names = []
        with os.scandir(parent_fd) as stream:
            for entry in stream:
                names.append(entry.name)
                if len(names) + len(found) > MAX_ENTRIES:
                    reject("inventory_limit")
        for name in sorted(names):
            if len(found) >= MAX_ENTRIES or time.monotonic() > deadline:
                reject("inventory_limit")
            path = parent / name
            relative = path.relative_to(root).as_posix()
            if any(vault_scope.inside(relative, prefix) for prefix in excluded):
                reject("protected_entries_present")
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if info.st_dev != root_key[0] or stat.S_ISLNK(info.st_mode):
                reject("links_or_mounts")
            if getattr(info, "st_flags", 0) & cloud_files.SF_DATALESS or name.endswith(".icloud"):
                reject("local_folder_required")
            relative = path.relative_to(root).as_posix()
            value: dict[str, Any] = {
                "path": relative,
                "dev": info.st_dev,
                "ino": info.st_ino,
                "mode": stat.S_IMODE(info.st_mode),
            }
            if not (
                stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            ):
                reject("links_or_mounts")
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            fd = os.open(
                name,
                flags | (os.O_DIRECTORY if stat.S_ISDIR(info.st_mode) else 0),
                dir_fd=parent_fd,
            )
            try:
                before = os.fstat(fd)
                if (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
                    reject("source_changed")
                if stat.S_ISDIR(info.st_mode):
                    value["kind"] = "directory"
                    found.append(value)
                    walk(fd, path, depth + 1)
                else:
                    total += info.st_size
                    if total > MAX_BYTES:
                        reject("inventory_limit")
                    digest = hashlib.sha256()
                    count = 0
                    tail = b""
                    needle = root.as_posix().encode()
                    while chunk := os.read(fd, 1024 * 1024):
                        count += len(chunk)
                        if count > info.st_size or time.monotonic() > deadline:
                            reject("source_changed")
                        digest.update(chunk)
                        if needle in tail + chunk and path.suffix.lower() in {
                            ".md",
                            ".canvas",
                            ".json",
                            ".html",
                            ".css",
                            ".txt",
                        }:
                            reject("absolute_links")
                        tail = chunk[-len(needle) :]
                    after = os.fstat(fd)
                    before_fields = (
                        before.st_dev,
                        before.st_ino,
                        before.st_size,
                        before.st_mtime_ns,
                        before.st_ctime_ns,
                    )
                    after_fields = (
                        after.st_dev,
                        after.st_ino,
                        after.st_size,
                        after.st_mtime_ns,
                        after.st_ctime_ns,
                    )
                    if before_fields != after_fields or count != info.st_size:
                        reject("source_changed")
                    value.update(
                        kind="file",
                        bytes=info.st_size,
                        mtime_ns=info.st_mtime_ns,
                        birth=getattr(info, "st_birthtime", None),
                        sha256=digest.hexdigest(),
                    )
                    found.append(value)
            finally:
                os.close(fd)

    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if [os.fstat(root_fd).st_dev, os.fstat(root_fd).st_ino] != root_key:
            reject("directory_changed")
        walk(root_fd, root, 0)
    finally:
        os.close(root_fd)
    if key(root) != root_key:
        reject("source_changed")
    if icloud:
        from .icloud import available

        available(root, excluded)
    return found


def move(
    source: Path,
    destination: Path,
    source_parent: list[int],
    target_parent: list[int],
    root_key: list[int],
) -> None:
    if key(source.parent) != source_parent or key(destination.parent) != target_parent:
        reject("parent_changed")
    if key(source) != root_key:
        reject("directory_changed")
    a = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        b = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if [os.fstat(a).st_dev, os.fstat(a).st_ino] != source_parent or [
                os.fstat(b).st_dev,
                os.fstat(b).st_ino,
            ] != target_parent:
                reject("parent_changed")
            current = os.stat(source.name, dir_fd=a, follow_symlinks=False)
            if not stat.S_ISDIR(current.st_mode) or [current.st_dev, current.st_ino] != root_key:
                reject("directory_changed")
            file_exchange.rename(a, source.name, b, destination.name, exchange=False)
        finally:
            os.close(b)
    finally:
        os.close(a)
