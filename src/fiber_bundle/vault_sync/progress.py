"""Durable completion/backoff evidence for incremental slices, separate from index bytes."""

import time
from typing import Any

from .. import note_search_jobs
from ..config import Config
from . import problems, store


def complete(
    config: Config,
    identity: str,
    row: dict[str, Any],
    files: dict[str, Any],
    issues: dict[str, Any],
    chosen: set[str],
    removed: set[str],
    errors: dict[str, str],
    pending: int,
    counts: dict[str, object],
    job_id: str,
) -> int:
    with store.connection(config, write=True) as side:
        assert side is not None
        for path in chosen | removed:
            side.execute("DELETE FROM issues WHERE vault=? AND path=?", (identity, path))
        for path, code in errors.items():
            previous = issues.get(path)
            attempts = (
                previous["attempts"] + 1
                if previous and previous["revision"] == files[path]["revision"]
                else 1
            )
            delay = store.DELAYS[min(attempts - 1, len(store.DELAYS) - 1)]
            if problems.category(code) == "review":
                delay = 365 * 86400
            side.execute(
                "INSERT INTO issues VALUES(?,?,?,?,?,?)",
                (identity, path, code, files[path]["revision"], time.time() + delay, attempts),
            )
        attention = side.execute(
            "SELECT count(*) FROM issues WHERE vault=?", (identity,)
        ).fetchone()[0]
        side.execute(
            "UPDATE libraries SET indexed=?,state=?,error=NULL,index_attempts=0 WHERE id=?",
            (
                row["indexed"] if pending else row["generation"],
                "index_pending" if pending else "attention" if attention else "ready",
                identity,
            ),
        )
    note_search_jobs.record(
        config,
        identity,
        "building" if pending else "partial" if attention else "ready",
        reason="source_changed",
        expected_id=job_id,
        result=counts,
    )
    return int(attention)


def details(config: Config, identity: str) -> dict[str, Any]:
    """Local metadata only; filenames are from the catalog, not a fresh iCloud scan."""
    import json

    from .. import note_search_cache as cache
    from ..inputs import canonical

    with store.connection(config) as side:
        assert side is not None
        issues = [
            dict(r)
            for r in side.execute(
                "SELECT path,reason,next_check,attempts FROM issues WHERE vault=? ORDER BY path",
                (identity,),
            )
        ]
        files = {
            r["path"]: canonical(json.loads(r["signature"])[:8])
            for r in side.execute(
                "SELECT path,signature FROM files WHERE vault=? AND directory=0 "
                "AND lower(path) LIKE '%.md'",
                (identity,),
            )
        }
    observed = {}
    with cache.connection(config, identity) as index:
        if index is not None:
            observed = dict(index.execute("SELECT path,file_stat FROM observations"))
    groups = {
        kind: sum(problems.category(r["reason"]) == kind for r in issues)
        for kind in ("cloud", "review", "changing", "unavailable")
    }
    return {
        "pending_checks": sum(value != observed.get(path) for path, value in files.items()),
        "issue_groups": groups,
        "issues": issues[:20],
        "issues_total": len(issues),
        "issues_truncated": len(issues) > 20,
    }
