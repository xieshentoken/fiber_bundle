"""Add automatic ownership to bounded task lists; schema30 keeps its original behavior."""

import sqlite3
from typing import Any


def automatic(
    db: sqlite3.Connection, kind: str, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if (
        not rows
        or not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='automatic_jobs'"
        ).fetchone()
    ):
        return rows
    ids = [r.get("run_id", r.get("job_id")) for r in rows]
    placeholders = ",".join("?" for _ in ids)
    sql = (
        "SELECT j.source_id,j.state FROM automatic_jobs j WHERE j.kind=? AND j.source_id IN ("
        + placeholders
        + ")"
    )
    if kind == "document":
        sql = (
            "SELECT COALESCE(s.run_id,j.source_id) AS source_id,j.state FROM automatic_jobs j "
            "LEFT JOIN source_imports s ON s.id=j.source_id WHERE j.kind=? AND "
            "COALESCE(s.run_id,j.source_id) IN (" + placeholders + ")"
        )
    jobs = {r["source_id"]: r["state"] for r in db.execute(sql, (kind, *ids))}
    return [{**r, "automatic_state": jobs.get(r.get("run_id", r.get("job_id")))} for r in rows]
