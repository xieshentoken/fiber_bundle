"""Explicit connection lifetime for one bounded, serial index reconciliation batch."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from .. import database
from ..config import Config, IntegrityError, contained
from ..note_read_session import signature
from ..publication_inventory_read import reuse as publication_reads


@dataclass(frozen=True)
class Batch:
    db: sqlite3.Connection
    path: Path
    identity: tuple[int | float, ...]

    def checked(self) -> sqlite3.Connection:
        if signature(self.path)[:2] != self.identity:
            raise IntegrityError("publication_location_database_changed")
        return self.db


current: ContextVar[Batch | None] = ContextVar("vault_reconcile_reads", default=None)


@contextmanager
def connection(config: Config) -> Iterator[sqlite3.Connection]:
    path = contained(config.state_root, "metadata.sqlite3")
    identity = signature(path)[:2]
    db = database.connect(config.state_root)
    batch = Batch(db, path, identity)
    token = current.set(batch)
    try:
        batch.checked()
        with publication_reads(db):
            yield db
    finally:
        current.reset(token)
        db.close()
