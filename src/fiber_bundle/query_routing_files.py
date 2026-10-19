"""Bounded file snapshots for routing policies, answers and explicit credentials."""

import os
import stat
from pathlib import Path

from .config import InvalidInput


def _open(path: Path) -> int:
    """Anchor every component to a directory descriptor without following links."""
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = os.open(path.anchor, directory_flags)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, directory_flags, dir_fd=parent)
            os.close(parent)
            parent = child
        return os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def read(path: Path, max_bytes: int, *, allow_empty: bool = False, private: bool = False) -> bytes:
    candidate = path.absolute()
    if candidate.resolve() != candidate:
        raise InvalidInput("routing_file_path_invalid")
    fd = _open(candidate)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or (private and (before.st_uid != os.getuid() or before.st_mode & 0o077))
            or not (0 if allow_empty else 1) <= before.st_size <= max_bytes
        ):
            raise InvalidInput("routing_file_size_or_type_invalid")
        raw = stream.read(max_bytes + 1)
        after = os.fstat(stream.fileno())
        if len(raw) != before.st_size or _identity(before) != _identity(after):
            raise InvalidInput("routing_file_changed")
        # Rewalk the pathname: the original descriptor can outlive a renamed parent.
        try:
            current = _open(candidate)
        except OSError as exc:
            raise InvalidInput("routing_file_changed") from exc
        try:
            if _identity(os.fstat(current)) != _identity(after):
                raise InvalidInput("routing_file_changed")
        finally:
            os.close(current)
    return raw
