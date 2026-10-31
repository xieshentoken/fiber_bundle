"""Current, metadata-only admission of complete or partially available note indexes."""

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from . import note_search_cache as cache
from . import vault_scope, vaults
from .config import Config
from .inputs import canonical
from .objects import digest
from .vault_sync import store, visibility


@dataclass(frozen=True)
class State:
    token: str
    omitted: frozenset[str]
    coverage: dict[str, Any] | None


def read(config: Config, db: sqlite3.Connection, vault: sqlite3.Row, index: Any) -> State:
    status = cache.status(config, db, vault)
    generation = index.execute("SELECT * FROM generation").fetchone()
    if (
        status["state"] not in {"ready", "partial"}
        or not generation
        or generation["state"] != "ready"
    ):
        raise cache.IndexUnavailable("note_index_update_required")
    if store.library(config, vault["id"]) is None:
        return State(str(generation["fingerprint"]), frozenset(), None)
    if not index.execute("SELECT 1 FROM sqlite_master WHERE name='sync_metadata'").fetchone():
        raise cache.IndexUnavailable("note_index_update_required")
    proof = visibility.read(config, db, vault).token
    saved = index.execute("SELECT visibility FROM sync_metadata").fetchone()
    if saved is None or saved[0] != proof:
        raise cache.IndexUnavailable("note_index_update_required")
    scope = vault_scope.for_vault(vaults.checked_vault(config, vault), vault)
    with store.connection(config) as side:
        assert side is not None
        issues = {
            r[0]: r[1]
            for r in side.execute("SELECT path,reason FROM issues WHERE vault=?", (vault["id"],))
            if scope.reachable(r[0])
        }
        pending = [
            r[0]
            for r in side.execute("SELECT path FROM pending WHERE vault=?", (vault["id"],))
            if scope.reachable(r[0])
        ]
        current = {
            r[0]: canonical(json.loads(r[1])[:8])
            for r in side.execute(
                "SELECT path,signature FROM files WHERE vault=? AND directory=0", (vault["id"],)
            )
            if scope.allows(r[0])
        }
    observations = dict(index.execute("SELECT path,file_stat FROM observations"))
    omitted = {
        path
        for path, signature in observations.items()
        if signature != current.get(path)
        or any(not p or path == p or path.startswith(p + "/") for p in [*issues, *pending])
    }
    for path in omitted:
        issues.setdefault(path, "vault_sync_source_changed")
    sync = status["sync"]
    return State(
        digest(
            canonical(
                [
                    generation["fingerprint"],
                    proof,
                    sync["generation"],
                    sorted(issues.items()),
                    sorted(pending),
                ]
            ).encode()
        ),
        frozenset(omitted),
        {
            "complete": status["coverage_complete"] and not omitted,
            "unavailable_count": len(issues),
            "unavailable": [{"path": p, "reason": r} for p, r in sorted(issues.items())][:100],
        },
    )
