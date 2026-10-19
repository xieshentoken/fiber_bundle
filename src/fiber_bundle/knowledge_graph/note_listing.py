"""Current authorized metadata pages for selecting graph inputs; no implicit registration."""

import sqlite3
from typing import Any

from .. import auth, note_provenance, note_search_inventory
from ..config import Config
from . import jobs


def listing(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vault_id: str,
    query: str = "",
    offset: int = 0,
) -> list[dict[str, Any]]:
    jobs.authorize(config, client, vault_id)
    vault = auth.grant(db, client, vault_id, "query")
    inventory = note_search_inventory.read(config, db, vault, selected=set())
    words = query.casefold().split()
    notes = sorted(inventory.notes, key=lambda n: (n.identity is None, n.relative_path))
    matched = [
        n for n in notes if all(w in (n.title + " " + n.relative_path).casefold() for w in words)
    ]
    rows = []
    for note in matched[offset : offset + 100]:
        try:
            provenance = note_provenance.describe(config, db, client, vault, inventory, note)
        except auth.Denied:
            continue
        rows.append(
            {
                "note_id": note.identity,
                "note_path": note.relative_path,
                "title": note.title,
                "sha256": note.sha256,
                "source_kind": provenance["source_kind"],
                "selectable": note.identity is not None,
            }
        )
    return rows
