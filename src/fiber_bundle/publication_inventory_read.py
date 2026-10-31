"""Compact read projection of committed locations; recovery/output bytes stay in SQLite."""

import json
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path
from typing import Any

from .config import IntegrityError

# These are exactly the plan fields used by project_inventory, not a new ledger format.
FIELDS = (
    "layout_version",
    "mapping",
    "published_hashes",
    "file_keys",
    "identity_overrides",
    "retained_source_root",
    "body_files",
    "body_projections",
    "policy_binding.settings",
    "supplement_roles",
)
PATHS = ",".join("'$." + field + "'" for field in FIELDS)


# Only the cooperative execution unit opts in. No file bodies or complete
# recovery payloads are retained, and the cache never survives the unit.
Row = tuple[dict[str, Any], dict[str, Any]]


class ProjectionRead:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        self.entries: dict[str, tuple[Any, list[Row]]] = {}

    def version(self) -> Any:
        # total_changes does not advance on SAVEPOINT rollback. Never reuse
        # projections inside transactions, including caller-owned transactions.
        if self.db.in_transaction:
            return None
        path = next(row[2] for row in self.db.execute("PRAGMA database_list") if row[1] == "main")
        if not path:
            return None
        info = Path(path).lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return None
        return (
            self.db.execute("PRAGMA data_version").fetchone()[0],
            self.db.total_changes,
            self.db.execute("PRAGMA schema_version").fetchone()[0],
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )

    def rows(self, vault: str) -> list[Row]:
        before = self.version()
        previous = self.entries.get(vault)
        if before is not None and previous is not None and previous[0] == before:
            result = deepcopy(previous[1])
            if self.version() == before:
                return result
        self.entries.pop(vault, None)
        result = list(_rows(self.db, vault))
        if before is not None and self.version() == before:
            self.entries[vault] = (before, deepcopy(result))
        return result


_active: ContextVar[ProjectionRead | None] = ContextVar("publication_projection_read", default=None)


@contextmanager
def reuse(db: sqlite3.Connection) -> Iterator[None]:
    """Explicit unit lifetime; ordinary readers always read their current SQLite snapshot."""
    token = _active.set(ProjectionRead(db))
    try:
        yield
    finally:
        _active.reset(token)


def rows(db: sqlite3.Connection, vault: str) -> Iterator[Row]:
    current = _active.get()
    if current is not None and current.db is db:
        yield from current.rows(vault)
    else:
        yield from _rows(db, vault)


def _rows(db: sqlite3.Connection, vault: str) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    """Project the current SQL values; never infer stability from a row revision."""
    sql = (
        "SELECT kind,publication_id,vault_id,entry_note_path,package_root,revision,state,"
        f"json_extract(value,{PATHS}) AS projected,"
        "json_type(value,'$.retained_source_root') AS retained_type,"
        "json_type(value,'$.body_files') AS body_type,"
        "json_type(value,'$.outputs') AS output_type,"
        "json_array(json_type(value,'$.file_keys'),json_type(value,'$.identity_overrides'),"
        "json_type(value,'$.body_projections'),"
        "json_type(value,'$.supplement_roles')) AS optional_types,"
        "CASE WHEN json_extract(value,'$.layout_version')=3 THEN '[]' ELSE "
        "(SELECT json_group_array(key) FROM json_each(l.value,'$.outputs')) END AS output_keys "
        "FROM publication_locations l WHERE vault_id=?"
    )
    for raw in db.execute(sql, (vault,)):
        values = json.loads(raw["projected"])
        plan = dict(zip(FIELDS, values, strict=True))
        if raw["output_type"] != "object" or not isinstance(plan["mapping"], dict):
            raise IntegrityError("publication location projection invalid")
        plan["outputs"] = dict.fromkeys(json.loads(raw["output_keys"]))
        # Missing and explicitly empty body_files have different visibility semantics.
        for name, kind in (("body_files", "body_type"), ("retained_source_root", "retained_type")):
            if raw[kind] is None:
                plan.pop(name)
        optional = ("file_keys", "identity_overrides", "body_projections", "supplement_roles")
        for name, kind in zip(optional, json.loads(raw["optional_types"]), strict=True):
            if plan[name] is None:
                if kind is not None:
                    raise IntegrityError("publication location projection invalid")
                plan[name] = {}
        settings = plan.pop("policy_binding.settings")
        plan["policy_binding"] = {"settings": settings}
        yield dict(raw), plan
