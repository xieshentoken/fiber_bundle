"""Current, metadata-only admission of complete or partially available note indexes."""

import sqlite3
from dataclasses import dataclass
from typing import Any

from . import note_search_cache as cache
from .config import Config
from .vault_sync import store


@dataclass(frozen=True)
class State:
    token: str
    omitted: frozenset[str]
    coverage: dict[str, Any] | None


def read(config: Config, db: sqlite3.Connection, vault: sqlite3.Row, index: Any) -> State:
    if store.library(config, vault["id"]) is not None:
        from . import indexed_sources

        published = indexed_sources.read(config, db, None, vault)
        paths = {r[0] for r in index.execute("SELECT path FROM notes")}
        return State(published.token, frozenset(paths - published.notes.keys()), published.coverage)
    status = cache.status(config, db, vault)
    generation = index.execute("SELECT * FROM generation").fetchone()
    if (
        status["state"] not in {"ready", "partial"}
        or not generation
        or generation["state"] != "ready"
    ):
        raise cache.IndexUnavailable("note_index_update_required")
    return State(str(generation["fingerprint"]), frozenset(), None)
