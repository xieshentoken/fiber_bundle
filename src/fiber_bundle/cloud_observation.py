"""Bounded cloud read diagnostics; retries are confined to one read-only operation."""

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from typing import Any

from .config import IntegrityError

_root: ContextVar[Path | None] = ContextVar("cloud_diagnostic_root", default=None)
_phase: ContextVar[str] = ContextVar("cloud_diagnostic_phase", default="file_check")


@contextmanager
def scope(root: Path) -> Iterator[None]:
    token = _root.set(root)
    try:
        yield
    finally:
        _root.reset(token)


@contextmanager
def phase(name: str) -> Iterator[None]:
    token = _phase.set(name)
    try:
        yield
    finally:
        _phase.reset(token)


class ObservationError(IntegrityError):
    def __init__(
        self, code: str, path: Path | None, reason: str, *, changed: list[str] | None = None
    ) -> None:
        super().__init__(code)
        root = _root.get()
        relative = (
            str(path.relative_to(root)) if path and root and path.is_relative_to(root) else None
        )
        self.detail: dict[str, Any] = {
            "reason": reason,
            "phase": _phase.get(),
            "path": relative,
            "changed_fields": (changed or [])[:9],
            "observed_at": time.time(),
            "retryable_read": reason in {"stale", "metadata_unavailable", "metadata_changed"},
        }


def directory_changed(
    path: Path,
    before: tuple[int | float, ...],
    after: tuple[int | float, ...],
    *,
    code: str = "note directory changed",
) -> ObservationError:
    """Describe a failed census; a retry must recensus, never accept the old names."""
    keys = ["device", "inode", "mode", "links", "size", "mtime_ns", "ctime_ns", "flags", "birth"]
    fields = [key for key, old, new in zip(keys, before, after, strict=True) if old != new]
    error = ObservationError(code, path, "directory_changed", changed=fields)
    # Identity and permissions remain conflicts. APFS changes directory link
    # counts even for a regular hidden file; only a fresh complete census and
    # the caller's original content/name proof may establish that it is benign.
    error.detail["retryable_read"] = bool(fields) and set(fields) <= {
        "links",
        "size",
        "mtime_ns",
        "ctime_ns",
    }
    return error


def changed(path: Path, before: list[int], after: list[int]) -> ObservationError:
    keys = ["device", "inode", "mode", "links", "size", "mtime_ns", "ctime_ns"]
    fields = [key for key, old, new in zip(keys, before, after, strict=True) if old != new]
    return ObservationError(
        "cloud_item_pending_stale_or_conflicted", path, "metadata_changed", changed=fields
    )


def retry_read[**P, T](function: Callable[P, T]) -> Callable[P, T]:
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        from .note_read_session import current

        session = current.get()
        errors = kwargs.get("note_errors")
        initial_errors = dict(errors) if isinstance(errors, dict) else None
        try:
            return function(*args, **kwargs)
        except ObservationError as error:
            # No model, write, download or task replay. One retry total per unit,
            # only while executing a freshly authorized read with original CAS checks.
            if (
                session is None
                or not error.detail["retryable_read"]
                or session.counts.get("cloud_rechecks", 0)
            ):
                raise
            session.counts["cloud_rechecks"] = 1
            if isinstance(errors, dict) and initial_errors is not None:
                errors.clear()
                errors.update(initial_errors)
            session.invalidate()
            time.sleep(0.25)
            return function(*args, **kwargs)

    return wrapped
