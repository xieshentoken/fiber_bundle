"""Explicit single-note priority recheck. No invented filesystem event or recursive scan."""

import time
from pathlib import Path

from ..config import Config, IntegrityError
from . import store


def request(config: Config, vault: str, root: Path, path: str) -> int:
    with store.connection(config, write=True) as db:
        assert db is not None
        row = db.execute("SELECT * FROM libraries WHERE id=?", (vault,)).fetchone()
        if row is None or row["root"] != str(root) or not row["baseline"]:
            raise IntegrityError("cloud_recovery_index_not_ready")
        scope = store.scope_of(dict(row))
        if not scope.allows(path) or not store.accepted(scope, path) or not path.endswith(".md"):
            raise IntegrityError("cloud_recovery_note_required")
        existing = db.execute(
            "SELECT * FROM pending WHERE vault=? AND path=?", (vault, path)
        ).fetchone()
        now = time.time()
        # Repeated button presses do not postpone an already expedited check.
        if existing and existing["due"] <= now + store.QUIET:
            return int(existing["revision"])
        generation = row["generation"] + 1
        db.execute(
            "INSERT INTO pending VALUES(?,?,?,?,?,0) ON CONFLICT(vault,path) DO UPDATE SET "
            "revision=excluded.revision,due=excluded.due",
            (vault, path, 0, generation, now + store.QUIET),
        )
        db.execute(
            "UPDATE issues SET next_check=? WHERE vault=? AND path=?",
            (now + store.QUIET, vault, path),
        )
        db.execute(
            "UPDATE libraries SET generation=?,state='pending',index_after=0 WHERE id=?",
            (generation, vault),
        )
        return int(generation)
