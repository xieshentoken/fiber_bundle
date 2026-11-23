"""Task-local verification of unchanged content-addressed compensation files."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from ..config import IntegrityError
from ..note_read_session import signature


class VerifiedBlobs:
    """Retain signatures, never payloads; every use checks the actual file again."""

    def __init__(self) -> None:
        self.files: dict[Path, tuple[str, tuple[int | float, ...]]] = {}

    def check(self, path: Path, expected: str, read: Callable[[Path, str], str]) -> None:
        before = signature(path)
        if self.files.get(path) == (expected, before):
            if signature(path) == before:
                return
        read(path, expected)
        if signature(path) != before:
            self.files.pop(path, None)
            raise IntegrityError("maintenance_undo_blob_unavailable")
        if len(self.files) < 10000 or path in self.files:
            self.files[path] = expected, before


_active: ContextVar[VerifiedBlobs | None] = ContextVar("maintenance_undo_blob_reads", default=None)


@contextmanager
def reuse(reads: VerifiedBlobs | None = None) -> Iterator[None]:
    if _active.get() is not None:
        yield
        return
    token = _active.set(reads or VerifiedBlobs())
    try:
        yield
    finally:
        _active.reset(token)


def check(path: Path, expected: str, read: Callable[[Path, str], str]) -> None:
    current = _active.get()
    if current is None:
        read(path, expected)
    else:
        current.check(path, expected, read)
