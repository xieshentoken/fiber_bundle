"""Bounded read reuse within one organization unit; fresh cloud/CAS checks stay outside."""

import os
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING

from .config import IntegrityError

if TYPE_CHECKING:
    from .note_index import Note


def stat_signature(info: os.stat_result) -> tuple[int | float, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        getattr(info, "st_flags", 0),
        getattr(info, "st_birthtime", 0),
    )


def signature(path: Path) -> tuple[int | float, ...]:
    return stat_signature(path.lstat())


class ReadSession:
    def __init__(self) -> None:
        self.notes: dict[tuple[Path, int], tuple[tuple[int | float, ...], Note]] = {}
        self.directories: dict[Path, tuple[tuple[int | float, ...], list[str]]] = {}
        self.bytes = 0
        self.names_count = 0
        from .archive_link_planning import LinkPlanningSession

        self.links = LinkPlanningSession()
        self.counts = {
            "body_reads": 0,
            "body_reused": 0,
            "directory_scans": 0,
            "directory_reused": 0,
            **self.links.counts,
        }
        self.links.counts = self.counts

    def invalidate(self) -> None:
        """Discard derived observations before a bounded, fully fresh read."""
        self.notes.clear()
        self.directories.clear()
        self.bytes = self.names_count = 0
        self.links.parsed.clear()
        self.links.bytes = 0
        self.links.namespace = None
        self.links.children.clear()

    def note(self, path: Path, limit: int, read: Callable[[], "Note"]) -> "Note":
        # The caller has just checked cloud availability, scope and symlink ancestry.
        before = signature(path)
        saved = self.notes.get((path, limit))
        if saved and saved[0] == before:
            self.counts["body_reused"] += 1
            return saved[1]
        self.counts["body_reads"] += 1
        value = read()
        if signature(path) != before:
            raise IntegrityError("note changed while reading")
        if saved:
            self.bytes -= len(saved[1].text.encode("utf-8"))
            del self.notes[path, limit]
        size = len(value.text.encode("utf-8"))
        if self.bytes + size <= 32 * 1024 * 1024 and len(self.notes) < 10000:
            self.notes[path, limit] = (before, value)
            self.bytes += size
        return value

    def names(self, path: Path, maximum: int) -> Iterator[str]:
        from .cloud_files import require_local
        from .cloud_observation import directory_changed

        require_local(path)
        before = signature(path)
        if not stat.S_ISDIR(int(before[2])):
            raise directory_changed(path, before, before)
        saved = self.directories.get(path)
        if saved and saved[0] == before:
            self.counts["directory_reused"] += 1
            yield from saved[1]
            after = signature(path)
            if after != before:
                raise directory_changed(path, before, after)
            return
        self.counts["directory_scans"] += 1
        names: list[str] | None = []
        with os.scandir(path) as entries:
            for entry in entries:
                if names is not None:
                    names.append(entry.name)
                    if len(names) > maximum:
                        names = None  # Stream large excluded directories without caching them.
                yield entry.name
        after = signature(path)
        if after != before:
            raise directory_changed(path, before, after)
        if saved:
            self.names_count -= len(saved[1])
            del self.directories[path]
        if names is not None and self.names_count + len(names) <= 20000:
            self.directories[path] = before, names
            self.names_count += len(names)


current: ContextVar[ReadSession | None] = ContextVar("organization_read_session", default=None)


@contextmanager
def reuse(session: ReadSession | None = None) -> Iterator[ReadSession]:
    existing = current.get()
    if existing is not None:
        yield existing
        return
    session = session or ReadSession()
    token = current.set(session)
    try:
        yield session
    finally:
        current.reset(token)


def names(path: Path, maximum: int) -> Iterator[str]:
    session = current.get()
    if session is not None:
        yield from session.names(path, maximum)
    else:
        with os.scandir(path) as entries:
            for entry in entries:
                yield entry.name
