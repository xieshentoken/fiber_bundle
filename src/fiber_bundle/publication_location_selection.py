"""Select relevant committed-location IDs before loading their complete recovery payloads."""

import json
import sqlite3
from collections.abc import Collection
from typing import Any

from . import publication_locations
from .config import IntegrityError


def at_path(db: sqlite3.Connection, vault: str, path: str) -> list[dict[str, Any]]:
    identities = db.execute(
        "SELECT kind,publication_id FROM publication_locations WHERE vault_id=? "
        "AND (entry_note_path=? OR package_root=?) ORDER BY kind,publication_id",
        (vault, path, path),
    ).fetchall()
    return _load(db, identities)


def affected(db: sqlite3.Connection, vault: str, paths: Collection[str]) -> list[dict[str, Any]]:
    """Read every current mapping; select full rows only if an actual member may change.

    Completion calls this inside its existing transaction, so selection and full
    payloads share a SQLite snapshot. Revision numbers are never used as proof.
    """
    identities = []
    for row in db.execute(
        "SELECT kind,publication_id,json_extract(value,'$.mapping') AS mapping "
        "FROM publication_locations WHERE vault_id=? ORDER BY kind,publication_id",
        (vault,),
    ):
        mapping = json.loads(row["mapping"]) if isinstance(row["mapping"], str) else None
        if not isinstance(mapping, dict) or any(not isinstance(p, str) for p in mapping.values()):
            raise IntegrityError("publication location mapping invalid")
        if any(path in paths for path in mapping.values()):
            identities.append(row)
    return _load(db, identities)


def _load(db: sqlite3.Connection, identities: Any) -> list[dict[str, Any]]:
    result = []
    for row in identities:
        current = publication_locations.get(db, row["kind"], row["publication_id"])
        if current is None:
            raise IntegrityError("publication location selection changed")
        result.append(current)
    return result
