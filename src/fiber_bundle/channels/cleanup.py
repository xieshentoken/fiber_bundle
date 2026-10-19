"""Bounded credential cleanup under the original instance lock; preserve its inode and audits."""

import fcntl
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..config import InvalidInput, contained, make_directory, sync_directory


@contextmanager
def stopped_home(home: Path) -> Iterator[None]:
    if home.is_symlink():
        raise InvalidInput("channel_cleanup_requires_review")
    make_directory(home)
    fd = os.open(home / "fiber-instance.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InvalidInput("channel_process_not_stopped") from None
        yield
    finally:
        os.close(fd)


def _files(path: Path, depth: int = 0) -> list[Path]:
    if depth > 8:
        raise InvalidInput("channel_cleanup_requires_review")
    if not path.exists() and not path.is_symlink():
        return []
    info = path.lstat()
    if info.st_uid != os.getuid() or stat.S_ISLNK(info.st_mode):
        raise InvalidInput("channel_cleanup_requires_review")
    if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
        return [path]
    if not stat.S_ISDIR(info.st_mode):
        raise InvalidInput("channel_cleanup_requires_review")
    result: list[Path] = []
    for child in path.iterdir():
        result.extend(_files(child, depth + 1))
        if len(result) > 1000:
            raise InvalidInput("channel_cleanup_requires_review")
    return result


def identity_files(home: Path, extra: tuple[Path, ...] = ()) -> None:
    names = (
        "weixin",
        "weixin-login.json",
        "login-qr.png",
        "login-qr.tmp.png",
        "login-status.json",
        "pairing-candidate.json",
        "channel-cursor.json",
        "launch.json",
    )
    paths = [contained(home, name) for name in names] + list(extra)
    # Validate all targets before the first deletion. No recursion outside the dedicated home.
    files = [file for path in paths for file in _files(path)]
    for file in files:
        file.unlink(missing_ok=True)
        sync_directory(file.parent)
    # Empty token directories can remain; they contain no credentials and preserve path identity.
