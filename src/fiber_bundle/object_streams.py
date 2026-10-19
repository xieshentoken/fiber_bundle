"""Bounded immutable-object verification and import assembly; no destination from callers."""

import hashlib
import os
import stat
from collections.abc import Iterable
from pathlib import Path
from uuid import uuid4

from .config import IntegrityError, contained, make_directory, sync_directory
from .objects import object_relative

CHUNK_BYTES = 256 * 1024


def verify(path: Path, sha256: str, size: int) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size != size:
            raise IntegrityError("object type or length mismatch")
        digest = hashlib.sha256()
        while chunk := handle.read(CHUNK_BYTES):
            digest.update(chunk)
        after = os.fstat(handle.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise IntegrityError("object changed during verification")
        if digest.hexdigest() != sha256:
            raise IntegrityError("object hash mismatch")


def assemble(
    root: Path, vault_id: str, chunks: Iterable[tuple[Path, str, int]], sha256: str, size: int
) -> str:
    relative = object_relative(vault_id, sha256)
    destination = contained(root, relative)
    make_directory(destination.parent)
    path = contained(root, "staging/" + str(uuid4()) + ".part")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    digest = hashlib.sha256()
    total = 0
    with os.fdopen(fd, "wb") as output:
        for source, sha, length in chunks:
            verify(source, sha, length)
            source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(source_fd, "rb") as handle:
                part_digest = hashlib.sha256()
                part_size = 0
                while data := handle.read(CHUNK_BYTES):
                    total += len(data)
                    part_size += len(data)
                    if total > size or part_size > length:
                        raise IntegrityError("import assembly exceeded declared size")
                    digest.update(data)
                    part_digest.update(data)
                    output.write(data)
                if part_size != length or part_digest.hexdigest() != sha:
                    raise IntegrityError("import chunk changed")
        output.flush()
        os.fsync(output.fileno())
    if total != size or digest.hexdigest() != sha256:
        raise IntegrityError("import final digest mismatch")
    try:
        os.link(path, destination, follow_symlinks=False)
    except FileExistsError:
        verify(destination, sha256, size)
    sync_directory(destination.parent)
    path.unlink()
    sync_directory(path.parent)
    return relative
