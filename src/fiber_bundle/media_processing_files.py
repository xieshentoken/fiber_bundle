"""Exclusive bounded processing files; no arbitrary host path or redirect adoption."""

import os
import re
import stat
from pathlib import Path

from .config import InvalidInput, sync_directory

LIMIT = 8 * 1024 * 1024


def checked(root: Path, name: str) -> Path:
    if (
        root.resolve() != root
        or root.is_symlink()
        or not re.fullmatch(
            r"source|subtitle|request.json|result.json|raw.json|normalized.srt|frame-[0-9]{3}.jpg",
            name,
        )
    ):
        raise InvalidInput("media_processing_file_scope")
    return root / name


def read(root: Path, name: str, maximum: int = LIMIT) -> bytes:
    fd = os.open(checked(root, name), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as f:
        a = os.fstat(f.fileno())
        if not stat.S_ISREG(a.st_mode) or a.st_nlink != 1 or not 0 < a.st_size <= maximum:
            raise InvalidInput("media_processing_file_size_or_type")
        data = f.read(maximum + 1)
        b = os.fstat(f.fileno())
        if len(data) != a.st_size or (a.st_size, a.st_mtime_ns) != (b.st_size, b.st_mtime_ns):
            raise InvalidInput("media_processing_file_changed")
        return data


def write(root: Path, name: str, data: bytes, *, maximum: int = LIMIT) -> None:
    if not isinstance(data, bytes) or not 0 < len(data) <= maximum:
        raise InvalidInput("media_processing_file_size")
    fd = os.open(checked(root, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    sync_directory(root)
