"""Bounded user-supplied browser-copy read; no profile directories or authentication files."""

import os
import stat
from pathlib import Path

from .config import InvalidInput


def read(path: Path) -> bytes:
    if path.absolute() != path.absolute().resolve() or path.suffix.casefold() not in (
        ".wav",
        ".mp4",
    ):
        raise InvalidInput("media_browser_copy_file_scope")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        a = os.fstat(stream.fileno())
        if not stat.S_ISREG(a.st_mode) or a.st_nlink != 1 or not 0 < a.st_size <= 8 * 1024 * 1024:
            raise InvalidInput("media_browser_copy_file_size")
        data = stream.read(8 * 1024 * 1024 + 1)
        b = os.fstat(stream.fileno())
        if len(data) != a.st_size or (a.st_size, a.st_mtime_ns) != (b.st_size, b.st_mtime_ns):
            raise InvalidInput("media_browser_copy_file_changed")
        return data
