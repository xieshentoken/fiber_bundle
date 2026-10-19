"""Immutable document file inputs; chunked verification and exclusive capture copies."""

import hashlib
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .config import IntegrityError, sync_directory

CHUNK_BYTES = 256 * 1024


@dataclass(frozen=True)
class DocumentFile:
    path: Path
    sha256: str
    byte_count: int

    @contextmanager
    def open(self) -> Iterator[BinaryIO]:
        if (
            self.path.resolve() != self.path
            or self.path.is_symlink()
            or re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None
            or type(self.byte_count) is not int
            or self.byte_count < 1
        ):
            raise IntegrityError("document file binding")
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_size != self.byte_count
            ):
                raise IntegrityError("document file type or size")
            digest = hashlib.sha256()
            while chunk := stream.read(CHUNK_BYTES):
                digest.update(chunk)
            if digest.hexdigest() != self.sha256:
                raise IntegrityError("document file digest")
            stream.seek(0)
            yield stream
            after = os.fstat(stream.fileno())
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
                before.st_nlink,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
                after.st_nlink,
            ):
                raise IntegrityError("document file changed")

    def verify(self) -> None:
        with self.open():
            pass

    def read(self) -> bytes:
        with self.open() as stream:
            return stream.read(self.byte_count + 1)

    def copy_new(self, target: Path) -> None:
        with self.open() as source:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as output:
                digest = hashlib.sha256()
                size = 0
                while chunk := source.read(CHUNK_BYTES):
                    size += len(chunk)
                    if size > self.byte_count:
                        raise IntegrityError("document copy grew")
                    digest.update(chunk)
                    output.write(chunk)
                if size != self.byte_count or digest.hexdigest() != self.sha256:
                    raise IntegrityError("document copy changed")
                output.flush()
                os.fsync(output.fileno())
        sync_directory(target.parent)


def binding(source: bytes | DocumentFile, maximum: int) -> tuple[str, int]:
    if isinstance(source, DocumentFile):
        if source.byte_count > maximum:
            raise IntegrityError("document source size")
        source.verify()
        return source.sha256, source.byte_count
    if not isinstance(source, bytes) or not 0 < len(source) <= maximum:
        raise IntegrityError("document source size")
    return hashlib.sha256(source).hexdigest(), len(source)
