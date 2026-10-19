"""Document administrator read projections; callers hold the lock and authorize access."""

import sqlite3
from typing import Any

from .auth import Denied
from .config import IntegrityError


def listing(db: sqlite3.Connection) -> dict[str, Any]:
    rows = db.execute(
        "SELECT d.run_id,d.state,r.state AS run_state,r.client_id,r.vault_id,"
        "r.binding_kind,d.page_count,p.state AS publication_state "
        "FROM document_tasks d JOIN execution_runs r ON r.id=d.run_id LEFT JOIN "
        "document_publications p ON p.run_id=d.run_id "
        "ORDER BY r.created_at DESC LIMIT 50"
    ).fetchall()
    from .task_observations import automatic

    return {"tasks": automatic(db, "document", [dict(row) for row in rows])}


def run(db: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM execution_runs WHERE id=?", (run_id,)).fetchone()
    if not isinstance(row, sqlite3.Row):
        raise Denied(404)
    return row


def source_media_type(db: sqlite3.Connection, version_id: str, asset_id: str) -> str:
    row = db.execute(
        "SELECT media_type FROM version_assets WHERE version_id=? AND asset_id=? LIMIT 1",
        (version_id, asset_id),
    ).fetchone()
    if row is None:
        raise IntegrityError("document source metadata missing")
    return str(row[0])
