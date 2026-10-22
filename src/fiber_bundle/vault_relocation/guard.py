"""A durable per-Vault access barrier, shared by HTTP, workers and sidecars."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from .. import database
from ..config import Config
from .errors import RelocationError

TERMINAL = frozenset({"prepared", "complete", "restored", "cancelled"})
_executing: ContextVar[str | None] = ContextVar("vault_relocation_execution", default=None)
_receiving: ContextVar[str | None] = ContextVar("vault_relocation_receiving", default=None)


def operation(db: Any, vault: str) -> Any:
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='vault_operations'").fetchone():
        return None
    # Authorization needs the active barrier identity, never full recovery evidence.
    return db.execute(
        "SELECT id,vault_id,kind,state FROM vault_operations "
        "WHERE kind='vault_relocation' AND vault_id=? "
        "AND state NOT IN (?,?,?,?) LIMIT 1",
        (vault, *sorted(TERMINAL)),
    ).fetchone()


def check(db: Any, vault: str) -> None:
    row = operation(db, vault)
    if row and row["id"] != _executing.get() and vault != _receiving.get():
        raise RelocationError("relocation_in_progress", 409)


def paused(config: Config, vault: str) -> bool:
    with database.connect(config.state_root, readonly=True) as db:
        return operation(db, vault) is not None


def check_root(config: Config, row: Any, metadata: sqlite3.Connection | None = None) -> None:
    if metadata is None:
        with database.connect(config.state_root, readonly=True) as db:
            check_root(config, row, db)
        return
    check(metadata, row["id"])
    current = metadata.execute(
        "SELECT absolute_root FROM vaults WHERE id=?", (row["id"],)
    ).fetchone()
    if current is None or current[0] != row["absolute_root"]:
        raise RelocationError("relocation_location_changed", 409)


@contextmanager
def executing(identity: str) -> Iterator[None]:
    token = _executing.set(identity)
    try:
        yield
    finally:
        _executing.reset(token)


@contextmanager
def receiving(vault: str) -> Iterator[None]:
    """Only for durable raw-event admission: never read or write Vault content here."""
    token = _receiving.set(vault)
    try:
        yield
    finally:
        _receiving.reset(token)


def metadata_only(vault: str) -> bool:
    return _receiving.get() == vault


def revision(db: Any, vault: str) -> int:
    return int(
        db.execute(
            "SELECT COALESCE(MAX(json_extract(payload,'$.location_revision')),0) "
            "FROM vault_operations WHERE kind='vault_relocation' AND vault_id=?",
            (vault,),
        ).fetchone()[0]
    )


def selected_channel(config: Config, identity: str) -> str | None:
    from ..channels import store

    with store.readonly(config) as side:
        if side is None:
            return None
        row = side.execute("SELECT value FROM channels WHERE id=?", (identity,)).fetchone()
        return str(json.loads(row[0])["vault_id"]) if row else None
