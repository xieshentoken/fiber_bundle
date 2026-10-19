"""Bounded cache metadata only. Never checks Vault contents or schedules an update."""

import math
import re
import sqlite3
import time
from typing import Any

from . import note_search_cache, note_search_jobs
from .config import Config, IntegrityError, InvalidInput


def snapshot(config: Config, vault_id: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "vault_id": vault_id,
        "generation_state": "absent",
        "indexed_notes": None,
        "built_at": None,
        "update": None,
        "update_state": "unrecorded",
        "observed_at": time.time(),
        "freshness_verified": False,
    }
    try:
        with note_search_cache.connection(config, vault_id) as cache:
            if cache is not None:
                rows = cache.execute(
                    "SELECT fingerprint,state,built_at FROM generation LIMIT 2"
                ).fetchall()
                if len(rows) != 1:
                    result["generation_state"] = "incomplete"
                else:
                    row = rows[0]
                    if (
                        row["state"] not in {"ready", "stale", "building"}
                        or not isinstance(row["fingerprint"], str)
                        or not re.fullmatch(r"[0-9a-f]{64}", row["fingerprint"])
                        or type(row["built_at"]) not in {float, int}
                        or not math.isfinite(row["built_at"])
                        or row["built_at"] <= 0
                    ):
                        result["generation_state"] = "incomplete"
                    else:
                        count = cache.execute(
                            "SELECT count(*) FROM (SELECT 1 FROM notes LIMIT ?)",
                            (note_search_cache.MAX_CHUNKS + 1,),
                        ).fetchone()[0]
                        if count > note_search_cache.MAX_CHUNKS:
                            result["generation_state"] = "incomplete"
                        else:
                            result.update(
                                generation_state=row["state"],
                                indexed_notes=count,
                                built_at=row["built_at"],
                            )
    except note_search_cache.IndexVersionError:
        result["generation_state"] = "unsupported"
    except (OSError, sqlite3.Error, IntegrityError, InvalidInput):
        result["generation_state"] = "unreadable"
    try:
        update = note_search_jobs.read(config, vault_id)
        result["update"] = update
        result["update_state"] = update["state"] if update else "unrecorded"
    except (OSError, ValueError, IntegrityError, InvalidInput):
        result["update_state"] = "unreadable"
    return result


def listing(config: Config, db: sqlite3.Connection, offset: int, limit: int) -> dict[str, Any]:
    rows = db.execute(
        "SELECT id FROM vaults WHERE status='active' ORDER BY id LIMIT ? OFFSET ?",
        (limit + 1, offset),
    ).fetchall()
    return {
        "vaults": [snapshot(config, row["id"]) for row in rows[:limit]],
        "offset": offset,
        "limit": limit,
        "has_more": len(rows) > limit,
        "scope": "admin_active_vaults",
        "freshness_verified": False,
    }
