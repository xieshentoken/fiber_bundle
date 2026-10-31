"""Current cached note metadata plus explicit, freshly verified source bodies for graph jobs."""

import json
import sqlite3
from dataclasses import replace

from . import note_index, note_inventory, vaults
from . import note_search_cache as cache
from . import note_search_inventory_state as state
from .config import Config, IntegrityError
from .vault_sync.scanner import signature


def read(
    config: Config, db: sqlite3.Connection, vault: sqlite3.Row, *, selected: set[str]
) -> note_inventory.Inventory:
    with cache.connection(config, vault["id"]) as index:
        if index is None:
            from .vault_sync import store

            if store.library(config, vault["id"]) is not None:
                raise cache.IndexUnavailable("note_index_update_required")
            return note_inventory.read(config, db, vault)
        before = state.read(config, db, vault, index)
        root = vaults.checked_vault(config, vault)
        from .vault_scope import for_vault

        scope = for_vault(root, vault)
        notes = []
        observed = {}
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
            if cached.relative_path in before.omitted:
                if cached.identity in selected:
                    raise cache.IndexUnavailable("note_index_update_required")
                continue
            if cached.identity in selected:
                observed[cached.relative_path] = signature((root / cached.relative_path).lstat())
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
            before.coverage,
            before.token,
        )
        if state.read(config, db, vault, index).token != before.token:
            raise cache.IndexUnavailable("note_index_update_required")
        if any(signature((root / path).lstat()) != value for path, value in observed.items()):
            raise IntegrityError("note index source changed before delivery")
        return inventory
