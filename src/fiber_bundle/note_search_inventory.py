"""Current cached note metadata plus explicit, freshly verified source bodies for graph jobs."""

import json
import sqlite3
from dataclasses import replace

from . import note_index, note_inventory, vaults
from . import note_search_cache as cache
from .config import Config, IntegrityError


def read(
    config: Config, db: sqlite3.Connection, vault: sqlite3.Row, *, selected: set[str]
) -> note_inventory.Inventory:
    with cache.connection(config, vault["id"]) as index:
        if index is None:
            return note_inventory.read(config, db, vault)
        if cache.status(config, db, vault)["state"] != "ready":
            raise cache.IndexUnavailable("note_index_update_required")
        root = vaults.checked_vault(config, vault)
        from .vault_scope import for_vault

        scope = for_vault(root, vault)
        notes = []
        for number, row in enumerate(index.execute("SELECT metadata FROM notes ORDER BY path"), 1):
            if number > note_index.Limits().entries:
                raise IntegrityError("note index metadata limit")
            value = json.loads(row[0])
            value["file_key"] = tuple(value["file_key"]) if value["file_key"] is not None else None
            cached = note_index.Note(**value)
            if not scope.allows(cached.relative_path):
                raise cache.IndexUnavailable("note_index_update_required")
            if cached.text != "":
                raise IntegrityError("note index contains unexpected source body")
            if cached.identity in selected:
                current = note_index.read_note(
                    root, cached.relative_path, note_index.Limits().note_bytes
                )
                if (current.sha256, current.title, current.file_key) != (
                    cached.sha256,
                    cached.title,
                    cached.file_key,
                ):
                    raise IntegrityError("note index source changed")
                cached = replace(cached, text=current.text)
            notes.append(cached)
        from . import publication_locations

        publications = {
            row["id"]: row
            for row in db.execute(
                (
                    "SELECT p.* FROM article_publications p LEFT JOIN publication_locations l "
                    "ON l.kind='article' AND l.publication_id=p.id WHERE "
                    "COALESCE(l.vault_id,p.vault_id)=? AND p.state='published'"
                ),
                (vault["id"],),
            )
        }
        inventory = note_inventory.Inventory(
            root,
            notes,
            publications,
            {pid: publication_locations.binding(db, row) for pid, row in publications.items()},
            {
                note.publication_id: note.publication_path
                for note in notes
                if note.publication_id is not None and note.publication_path is not None
            },
        )
        if cache.status(config, db, vault)["state"] != "ready":
            raise cache.IndexUnavailable("note_index_update_required")
        return inventory
