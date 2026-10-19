"""Bounded client token file read for local operator commands; never diagnostic output."""

import os
import stat
from pathlib import Path

from .config import InvalidInput


def read(path: Path) -> str:
    if path.absolute().resolve() != path.absolute():
        raise InvalidInput("media_token_file_scope")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 1 <= info.st_size <= 256:
            raise InvalidInput("media_token_file_type_or_size")
        return stream.read(257).decode("ascii").strip()
