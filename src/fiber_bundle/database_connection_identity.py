"""Creation-time physical evidence for SQLite connections used by read projections."""

import sqlite3
from pathlib import Path

from .config import IntegrityError


class Connection(sqlite3.Connection):
    file_identity: tuple[int, int] | None = None


def before(path: Path) -> tuple[int, int] | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    return info.st_dev, info.st_ino


def bind(db: Connection, path: Path, expected: tuple[int, int] | None) -> None:
    actual = before(path)
    if expected is not None and actual != expected:
        raise IntegrityError("publication_location_database_changed")
    # A newly created database had no pre-open identity. It remains usable,
    # but this connection cannot opt in to a proven read-projection cache.
    db.file_identity = expected


def proved(db: sqlite3.Connection) -> tuple[int, int] | None:
    return db.file_identity if isinstance(db, Connection) else None
