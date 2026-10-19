"""Durable, exclusive directory publication. No authorization or database policy."""

import ctypes
import os
import stat
import sys
from pathlib import Path

from .article_capture import read_bounded, write_new
from .config import IntegrityError, contained, sync_directory


def stage(vault: Path, relative: str, files: dict[str, bytes]) -> None:
    root = contained(vault, relative)
    root.mkdir(mode=0o700)  # Never adopt an existing or interrupted staging directory.
    sync_directory(vault)
    for name, data in files.items():
        path = contained(root, name)
        if path.parent != root and not path.parent.exists():
            path.parent.mkdir(mode=0o700)
            sync_directory(root)
        write_new(path.parent, path.name, data)
    verify(vault, relative, files)
    sync_directory(root)


def verify(vault: Path, relative: str, files: dict[str, bytes]) -> None:
    root = contained(vault, relative)
    if not root.is_dir():
        raise IntegrityError("publication directory missing")
    expected = set(files)
    directories = {str(Path(name).parent) for name in files if Path(name).parent != Path(".")}
    actual: set[str] = set()

    def walk(directory: Path) -> None:
        for path in directory.iterdir():
            name = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode) and name in directories:
                actual.add(name)
                walk(path)
            elif stat.S_ISREG(mode) and name in expected:
                actual.add(name)
            else:
                raise IntegrityError("publication contains unexpected entry")

    walk(root)
    if actual != expected | directories:
        raise IntegrityError("publication files missing")
    for name, data in files.items():
        path = contained(root, name)
        if read_bounded(path.parent, path.name, len(data)) != data:
            raise IntegrityError("publication file changed")


def rename_exclusive(vault: Path, source: str, destination: str) -> None:
    """Atomic no-replace rename, including refusal to replace an empty directory.

    Both names are direct children of an opened Vault. Unsupported kernels/filesystems
    fail closed; there is deliberately no check-then-os.rename fallback.
    """
    for name in (source, destination):
        if "/" in name:
            raise IntegrityError("publication rename requires direct children")
        contained(vault, name)
    fd = os.open(vault, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if sys.platform == "darwin":
            rename = libc.renameatx_np
            flags = 4  # RENAME_EXCL, macOS sys/stdio.h
        elif sys.platform == "linux" and hasattr(libc, "renameat2"):
            rename = libc.renameat2
            flags = 1  # RENAME_NOREPLACE
        else:
            raise IntegrityError("exclusive directory rename unsupported")
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        if rename(fd, os.fsencode(source), fd, os.fsencode(destination), flags) != 0:
            error = ctypes.get_errno()
            raise OSError(error, "exclusive publication rename failed")
        os.fsync(fd)
    finally:
        os.close(fd)
