"""Worker-owned, bounded parsed bytes; never cache authorization or cloud readiness."""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from ..note_read_session import ReadSession, reuse


class ExecutionReads:
    """Retain one task's read cache between safe units, checking every signature again.

    The worker owns this object explicitly. No SQLite projection, scope decision,
    cloud observation or write proof survives a unit. Pause, failure, task changes
    and idle expiry discard it; restart simply reconstructs from current files.
    """

    def __init__(self) -> None:
        self.key: tuple[Any, ...] | None = None
        self.session: ReadSession | None = None
        self.used_at = 0.0

    def expire(self) -> None:
        if time.monotonic() - self.used_at > 60:
            self.clear()

    def clear(self) -> None:
        self.key = self.session = None

    @contextmanager
    def scope(self, config: Any, job: Any) -> Iterator[None]:
        self.expire()
        key = (
            str(config.state_root),
            job["vault_id"],
            job["client_id"],
            job["id"],
            job.get("generation", 0),
            job.get("plan_sha256"),
        )
        if key != self.key or self.session is None:
            self.key, self.session = key, ReadSession()
        try:
            with reuse(self.session):
                yield
        except BaseException:
            self.clear()
            raise
        finally:
            self.used_at = time.monotonic()
            if job["state"] not in {"queued", "running"} or job.get("stage") != "applying":
                self.clear()
