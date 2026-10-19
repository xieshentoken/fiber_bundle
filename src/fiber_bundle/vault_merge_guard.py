"""Visibility/write barrier spanning merge journals and sidecars; bypass is private to execution."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from .config import InvalidInput

_active: ContextVar[str | None] = ContextVar("fiber_merge_execution", default=None)
BLOCKING = {
    "pausing",
    "copying",
    "originals",
    "rebinding",
    "switching",
    "index",
    "reconcile",
    "rolling_back",
}


@contextmanager
def executing(identity: str) -> Iterator[None]:
    token = _active.set(identity)
    try:
        yield
    finally:
        _active.reset(token)


def check(db: Any, vault: str) -> None:
    from .vault_relocation.guard import check as relocation_check

    relocation_check(db, vault)
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='vault_operations'").fetchone() is None:
        return
    for row in db.execute("SELECT id,state,payload FROM vault_operations WHERE kind='vault_merge'"):
        if row["state"] not in BLOCKING or row["id"] == _active.get():
            continue
        plan = json.loads(row["payload"])["plan"]["selection"]
        if vault in [*plan["source_vault_ids"], plan["target_vault_id"]]:
            raise InvalidInput("vault_merge_in_progress_requires_reconciliation")


def original_visible(db: Any, version: str) -> bool:
    row = db.execute(
        "SELECT event_id FROM receipts WHERE source_version_id=? AND ingress_scope='vault-merge'",
        (version,),
    ).fetchone()
    if row is None:
        return True
    identity = row[0].split(":", 1)[0]
    parent = db.execute(
        "SELECT state FROM vault_operations WHERE id=? AND kind='vault_merge'", (identity,)
    ).fetchone()
    return bool(parent and (parent[0] == "complete" or _active.get() == identity))


def retained_paths(db: Any, vault: str) -> set[str]:
    result: set[str] = set()
    for row in db.execute(
        "SELECT payload FROM vault_operations WHERE kind='vault_merge' AND state='complete'"
    ):
        result.update(
            f["source_path"]
            for f in json.loads(row[0])["plan"]["files"]
            if f["source_vault_id"] == vault
        )
    return result
