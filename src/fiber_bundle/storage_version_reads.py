"""Bounded directory-name reuse for one local metadata validation unit."""

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .config import IntegrityError
from .note_read_session import signature


class DirectoryNames:
    def __init__(self) -> None:
        self.saved: dict[Path, tuple[tuple[int | float, ...], list[str]]] = {}
        self.count = 0

    def names(self, directory: Path) -> Iterator[str]:
        before = signature(directory)
        saved = self.saved.get(directory)
        if saved is not None and saved[0] == before:
            names = saved[1]
        else:
            if saved is not None:
                self.count -= len(saved[1])
                del self.saved[directory]
            collected: list[str] | None = (
                [] if stat.S_ISDIR(int(before[2])) and len(self.saved) < 10000 else None
            )
            available = 100000 - self.count
            with os.scandir(directory) as entries:
                for entry in entries:
                    if collected is not None:
                        if len(collected) >= available:
                            collected = None  # Stream without retaining an oversized directory.
                        else:
                            collected.append(entry.name)
                    yield entry.name
            if signature(directory) != before:
                raise IntegrityError("storage_version_directory_changed")
            if (
                collected is not None
                and len(self.saved) < 10000
                and self.count + len(collected) <= 100000
            ):
                self.saved[directory] = before, collected
                self.count += len(collected)
            return
        yield from names
        if signature(directory) != before:
            removed = self.saved.pop(directory, None)
            if removed is not None:
                self.count -= len(removed[1])
            # Refuse a mixed metadata version; the caller's read result is unusable.
            raise IntegrityError("storage_version_directory_changed")


_active: ContextVar[DirectoryNames | None] = ContextVar("storage_version_reads", default=None)


@contextmanager
def reuse() -> Iterator[None]:
    if _active.get() is not None:
        yield
        return
    token = _active.set(DirectoryNames())
    try:
        yield
    finally:
        _active.reset(token)


def names(directory: Path) -> Iterator[str]:
    current = _active.get()
    if current is not None:
        yield from current.names(directory)
    else:
        with os.scandir(directory) as entries:
            for entry in entries:
                yield entry.name
