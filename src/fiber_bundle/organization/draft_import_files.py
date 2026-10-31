"""Read-only external Draft inspection and bounded streaming copies; never run package code."""

import hashlib
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import cloud_files, file_exchange, publication_file_io, vault_scope
from ..config import IntegrityError, InvalidInput, contained
from ..inputs import canonical
from ..objects import digest
from . import packages

MAX_FILES = 10000
MAX_BYTES = 4 * 1024**3


def key(path: Path) -> list[int]:
    s = path.lstat()
    if not stat.S_ISDIR(s.st_mode):
        raise IntegrityError("draft_source_not_directory")
    return [s.st_dev, s.st_ino]


def signature(path: Path) -> list[int]:
    cloud_files.require_available(path)
    s = path.lstat()
    if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1:
        raise IntegrityError("draft_member_identity")
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]


def source_root(value: str) -> Path:
    path = Path(value)
    # Protect before resolve/stat, including a selected child of the protected tree.
    if (
        path == vault_scope.PROTECTED
        or vault_scope.PROTECTED in path.parents
        or any(
            path.parts[i : i + 2] == ("01-Projects", "protected-project")
            for i in range(len(path.parts) - 1)
        )
    ):
        raise InvalidInput("draft_source_protected")
    if not path.is_absolute():
        raise InvalidInput("draft_source_absolute_path_required")
    cloud_files.require_local(path)
    if path.resolve(strict=True) != path or not path.is_dir():
        raise InvalidInput("draft_source_unavailable_or_changed")
    return path


def inspect(value: str, vault: str, relative: str, vault_key: list[int]) -> dict[str, Any]:
    root = source_root(value)
    scope = vault_scope.effective(root, {"include": [""], "exclude": []})
    identity = key(root)
    observed: dict[str, list[int]] = {}
    total = 0

    def reader(name: str) -> bytes:
        nonlocal total
        if not scope.allows(name):
            raise InvalidInput("draft_source_scope_denied")
        path = contained(root, name)
        before = signature(path)
        if name in observed and observed[name] != before:
            raise IntegrityError("draft_source_changed")
        raw = publication_file_io.read(root, name)[0]
        if signature(path) != before:
            raise IntegrityError("draft_source_changed")
        if name not in observed:
            observed[name] = before
            total += len(raw)
        if len(observed) > MAX_FILES or total > MAX_BYTES:
            raise InvalidInput("draft_package_limit")
        return raw

    package = packages.inspect_reader(reader, vault, relative, vault_key)
    actual = inventory(root, scope)
    if set(actual) - {".DS_Store"} != set(package["files"]):
        raise InvalidInput("draft_package_unlisted_files")
    if key(root) != identity or any(
        signature(contained(root, n)) != s for n, s in observed.items()
    ):
        raise IntegrityError("draft_source_changed")
    return {
        "package": package,
        "source_key": identity,
        "source_files": observed,
        "source_path": str(root),
        "total_bytes": total,
        "source_digest": digest(canonical(observed).encode()),
        "ignored_files": [".DS_Store"] if ".DS_Store" in actual else [],
    }


def inventory(root: Path, scope: vault_scope.Scope) -> list[str]:
    result: list[str] = []
    count = 0

    def walk(parent: str, depth: int) -> None:
        nonlocal count
        if depth > 32:
            raise InvalidInput("draft_package_depth")
        directory = contained(root, parent) if parent else root
        cloud_files.require_available(directory)
        with os.scandir(directory) as entries:
            for entry in entries:
                name = parent + "/" + entry.name if parent else entry.name
                count += 1
                if count > MAX_FILES * 2:
                    raise InvalidInput("draft_package_limit")
                if not scope.allows(name):
                    raise InvalidInput("draft_source_scope_denied")
                path = contained(root, name)
                cloud_files.require_available(path)
                info = path.lstat()
                if stat.S_ISDIR(info.st_mode):
                    walk(name, depth + 1)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    result.append(name)
                else:
                    raise InvalidInput("draft_member_identity")

    walk("", 0)
    return result


def recheck(task: dict[str, Any]) -> Path:
    root = source_root(task["source_path"])
    if key(root) != task["source_key"]:
        raise IntegrityError("draft_source_changed")
    scope = vault_scope.effective(root, {"include": [""], "exclude": []})
    if set(inventory(root, scope)) - {".DS_Store"} != set(task["source_files"]):
        raise IntegrityError("draft_source_changed")
    for n, expected in task["source_files"].items():
        if signature(contained(root, n)) != expected:
            raise IntegrityError("draft_source_changed")
    return root


def copy_member(
    root: Path,
    name: str,
    target: Path,
    expected: dict[str, Any],
    source_signature: list[int],
    progress: Callable[[int], None],
) -> None:
    source = contained(root, name)
    if signature(source) != source_signature:
        raise IntegrityError("draft_source_changed")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.is_symlink():
        raise IntegrityError("draft_staging_changed")
    h = hashlib.sha256()
    parent = file_exchange.parent_fd(root, name)
    try:
        source_fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)
    target_parent = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        target_fd = os.open(
            target.name,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
            0o600,
            dir_fd=target_parent,
        )
    except BaseException:
        os.close(source_fd)
        raise
    finally:
        os.close(target_parent)
    with os.fdopen(source_fd, "rb") as src, os.fdopen(target_fd, "wb") as dst:
        info = os.fstat(src.fileno())
        if [
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        ] != source_signature:
            raise IntegrityError("draft_source_changed")
        while block := src.read(1024 * 1024):
            dst.write(block)
            h.update(block)
            progress(len(block))
        dst.flush()
        os.fsync(dst.fileno())
    if h.hexdigest() != expected["sha256"] or signature(source) != source_signature:
        raise IntegrityError("draft_source_changed")
