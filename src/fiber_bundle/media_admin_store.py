"""Media administrator read projections; callers hold the lock and authorize access."""

import sqlite3
from typing import Any

from .auth import Denied


def task(db: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM media_tasks WHERE id=?", (job_id,)).fetchone()
    if not isinstance(row, sqlite3.Row):
        raise Denied(404)
    return row


def listing(db: sqlite3.Connection) -> dict[str, Any]:
    from .task_observations import automatic

    return {
        "tasks": automatic(
            db,
            "media",
            [
                dict(row)
                for row in db.execute(
                    "SELECT p.job_id,p.state,m.client_id,m.vault_id,u.state AS publication_state "
                    "FROM media_processing p JOIN media_tasks m ON m.id=p.job_id LEFT JOIN "
                    "media_publications u ON u.job_id=p.job_id "
                    "ORDER BY p.created_at DESC LIMIT 50"
                )
            ],
        )
    }


def publication(db: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT state,relative_path FROM media_publications WHERE job_id=?", (job_id,)
    ).fetchone()
    from .publication_locations import wire

    return None if row is None else {**dict(row), **wire(db, "media", job_id)}
