"""Atomic file creation or exchange between opened directories; never replace blindly."""

import ctypes
import os
import stat
import sys
from pathlib import Path

from .config import IntegrityError


def parent_fd(root: Path, relative: str) -> int:
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in relative.split("/")[:-1]:
            new = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
        return fd
    except BaseException:
        os.close(fd)
        raise


def rename(source_fd: int, source: str, target_fd: int, target: str, *, exchange: bool) -> None:
    for name in (source, target):
        if not name or "/" in name or name in {".", ".."}:
            raise IntegrityError("atomic file operation requires direct names")
    if os.fstat(source_fd).st_dev != os.fstat(target_fd).st_dev:
        raise IntegrityError("MOC candidate and Vault must share a filesystem")
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        call = libc.renameatx_np
        flag = 2 if exchange else 4  # RENAME_SWAP / RENAME_EXCL
    elif sys.platform == "linux" and hasattr(libc, "renameat2"):
        call = libc.renameat2
        flag = 2 if exchange else 1  # RENAME_EXCHANGE / RENAME_NOREPLACE
    else:
        raise IntegrityError("atomic file exchange unsupported")
    call.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    call.restype = ctypes.c_int
    if call(source_fd, os.fsencode(source), target_fd, os.fsencode(target), flag) != 0:
        raise OSError(ctypes.get_errno(), "atomic MOC file operation failed")
    os.fsync(source_fd)
    os.fsync(target_fd)


def read(fd: int, name: str) -> tuple[bytes, tuple[int, int]]:
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(handle, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1024 * 1024:
            raise IntegrityError("MOC file is not a bounded independent regular file")
        data = stream.read(1024 * 1024 + 1)
        after = os.fstat(stream.fileno())
        if len(data) != info.st_size or (info.st_size, info.st_mtime_ns) != (
            after.st_size,
            after.st_mtime_ns,
        ):
            raise IntegrityError("MOC file changed while reading")
        data.decode("utf-8-sig")
        return data, (info.st_dev, info.st_ino)
