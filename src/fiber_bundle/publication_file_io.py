"""Bounded independent publication files, including binary and empty package members."""

import os
import stat
from pathlib import Path
from typing import Any

from . import cloud_files, file_exchange, vault_directories, vault_scope
from .config import IntegrityError, contained
from .objects import digest


def read(root: Path, relative: str) -> tuple[bytes, list[Any]]:
    path = contained(root, relative)
    cloud_files.require_available(path)
    parent = file_exchange.parent_fd(root, relative)
    try:
        handle = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)
    with os.fdopen(handle, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 128 * 1024 * 1024:
            raise IntegrityError("layout_file_limit_or_identity")
        data = stream.read(info.st_size + 1)
        after = os.fstat(stream.fileno())
        if len(data) != info.st_size or (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise IntegrityError("layout_file_changed")
        birth = getattr(info, "st_birthtime", None)
        return data, [info.st_dev, info.st_ino, float(birth if birth is not None else 0).hex()]


def tree(root: Path, relative: str, scope: vault_scope.Scope) -> dict[str, bytes]:
    """Read a managed package only; reject excluded children before inspecting them."""
    files: dict[str, bytes] = {}
    count = total = 0

    def walk(parent: str) -> None:
        nonlocal count, total
        if len(parent.split("/")) > 32:
            raise IntegrityError("layout_package_depth_limit")
        with (
            vault_directories.opened_parent(
                root, scope, parent, internal_root=relative + "/.fiber-expert-canonical"
            ) as (_, fd),
            os.scandir(fd) as entries,
        ):
            before = os.fstat(fd)
            names = []
            for entry in entries:
                count += 1
                if count > 4096:
                    raise IntegrityError("layout_package_entry_limit")
                path = parent + "/" + entry.name
                if not scope.allows(path):
                    raise IntegrityError("layout_package_scope_denied")
                names.append(path)
            for path in sorted(names):
                cloud_files.require_available(contained(root, path))
                info = os.stat(path.rsplit("/", 1)[-1], dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    walk(path)
                elif stat.S_ISREG(info.st_mode):
                    data, _ = read(root, path)
                    total += len(data)
                    if total > 128 * 1024 * 1024:
                        raise IntegrityError("layout_package_byte_limit")
                    files[path.removeprefix(relative + "/")] = data
                else:
                    raise IntegrityError("layout_package_file_type")
            after = os.fstat(fd)
            if (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
                raise IntegrityError("layout_package_changed")

    walk(relative)
    return files


def remove_owned(root: Path, path: str, key: list[Any], sha: str) -> None:
    from .file_exchange import parent_fd

    raw, current = read(root, path)
    if current != key or digest(raw) != sha:
        raise IntegrityError("vault_merge_owned_file_changed_before_removal")
    parent = parent_fd(root, path)
    try:
        info = os.stat(Path(path).name, dir_fd=parent, follow_symlinks=False)
        birth = getattr(info, "st_birthtime", None)
        current_key = [info.st_dev, info.st_ino, float(birth if birth is not None else 0).hex()]
        if current_key != key or info.st_nlink != 1:
            raise IntegrityError("vault_merge_owned_file_changed_before_removal")
        os.unlink(Path(path).name, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)
