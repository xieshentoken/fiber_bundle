"""Shared UTC admission count for built-in local answers and managed Agent turns.

Callers hold the storage lease through admission. Failed/unknown reservations count.
"""

import sqlite3
from datetime import UTC, datetime


def count(db: sqlite3.Connection, day: str) -> int:
    midnight = datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp()
    local = db.execute(
        "SELECT count(*) FROM local_query_calls WHERE billing_day=?", (day,)
    ).fetchone()[0]
    agent = db.execute(
        "SELECT count(*) FROM agent_connection_operations WHERE kind='test_query' "
        "AND created_at>=? AND created_at<? "
        "AND json_extract(payload_json,'$.cloud_reservation') IS NULL",
        (midnight, midnight + 86400),
    ).fetchone()[0]
    return int(local + agent)
