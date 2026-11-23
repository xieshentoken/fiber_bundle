"""Request-local observations for display reads; never enabled for preview or writes."""

import sqlite3
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from .config import IntegrityError

current: ContextVar[dict[Path, tuple[int, ...]] | None] = ContextVar(
    "display_observation", default=None
)

pending_indexes: ContextVar[set[str] | None] = ContextVar("display_pending_indexes", default=None)
authorizations: ContextVar[dict[tuple[str, str, str], Any] | None] = ContextVar(
    "display_authorizations", default=None
)


def remember_authorization(
    db: sqlite3.Connection, client: str, vault: str, action: str, row: Any
) -> None:
    observed = authorizations.get()
    if observed is None:
        return
    epoch = db.execute(
        "SELECT revision FROM graph_permission_epochs WHERE client_id=? AND vault_id=?",
        (client, vault),
    ).fetchone()
    value = (tuple(row), tuple(epoch) if epoch else None)
    key = (client, vault, action)
    if key in observed and observed[key] != value:
        raise IntegrityError("display_source_changed")
    if len(observed) >= 1000 and key not in observed:
        raise IntegrityError("display_observation_limit")
    observed[key] = value


def verify_authorizations(config: Any, observed: Any) -> None:
    from . import auth, database

    with database.connect(config.state_root, readonly=True) as db:
        for (client, vault, action), before in observed.items():
            row = auth.grant(db, client, vault, action)
            epoch = db.execute(
                "SELECT revision FROM graph_permission_epochs WHERE client_id=? AND vault_id=?",
                (client, vault),
            ).fetchone()
            if (tuple(row), tuple(epoch) if epoch else None) != before:
                raise IntegrityError("display_source_changed")


def error_code(error: Exception) -> str | None:
    """Recognize only our bounded observation failures, never arbitrary exception text."""
    if not isinstance(error, IntegrityError):
        return None
    return {
        "display_source_changed": "display_changed",
        "display_observation_limit": "display_limit_exceeded",
        "display_response_limit": "display_limit_exceeded",
    }.get(str(error))


def signature(path: Path) -> tuple[int, ...]:
    info = path.lstat()
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        getattr(info, "st_flags", 0),
    )


def remember(path: Path, before: tuple[int, ...]) -> None:
    observations = current.get()
    if observations is None:
        return
    if signature(path) != before or (path in observations and observations[path] != before):
        raise IntegrityError("display_source_changed")
    if len(observations) >= 20000 and path not in observations:
        raise IntegrityError("display_observation_limit")
    observations[path] = before


def reusable(path: Path) -> bool:
    observations = current.get()
    if observations is None or path not in observations:
        return False
    if signature(path) != observations[path]:
        raise IntegrityError("display_source_changed")
    return True


def verify(observations: Any) -> None:
    for path, before in observations.items():
        if signature(path) != before:
            raise IntegrityError("display_source_changed")


def preflight(parent: Path, paths: list[Path]) -> None:
    if current.get() is None:
        return
    from .cloud_files import require_available_many, require_local

    require_local(parent)
    before = signature(parent)
    names = tuple(path.name for path in paths if not reusable(path))
    require_available_many(parent, names)
    remember(parent, before)
