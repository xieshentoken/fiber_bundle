"""Read-only admission of cached note metadata; never fall back to a body scan."""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import auth, indexed_sources, note_changes, note_index, note_search_cache, vaults
from ..agent_connections.visibility import scope as connection_scope
from ..config import Config, IntegrityError
from ..inputs import canonical
from ..objects import digest
from ..vault_scope import for_vault
from . import cloud_experts, cloud_visibility

MAX_NOTES = 20_000


@dataclass(frozen=True)
class Snapshot:
    root: Path
    notes: dict[str, note_index.Note]
    token: str
    coverage: dict[str, Any]
    experts: dict[str, dict[str, Any]]


def identity(vault: str, note: note_index.Note) -> str:
    return note.identity or "index:" + digest(canonical([vault, note.relative_path]).encode())


def read(config: Config, db: sqlite3.Connection, client: str, vault_id: str) -> Snapshot:
    note_changes.identifier(client)
    note_changes.identifier(vault_id)
    vault = auth.grant(db, client, vault_id, "query")
    root = vaults.checked_vault(config, vault)
    empty = {
        "scope": "indexed_sources",
        "complete": False,
        "index_state": "absent",
        "unavailable_count": 1,
        "unavailable": [{"reason": "index_unavailable"}],
    }
    if connection_scope(config, db, client, [vault_id])[1] != "all":
        return Snapshot(
            root,
            {},
            "quality_restricted",
            {
                **empty,
                "index_state": "unavailable",
                "unavailable": [{"reason": "quality_scope_requires_verified_projection"}],
            },
            {},
        )
    with note_search_cache.connection(config, vault_id) as index:
        if index is None:
            return Snapshot(root, {}, "unavailable", empty, {})
        generation = index.execute("SELECT * FROM generation").fetchone()
        if generation is None or generation["state"] not in {"ready", "stale"}:
            return Snapshot(root, {}, "unavailable", {**empty, "index_state": "unavailable"}, {})
        if indexed_sources.managed(config, vault_id):
            current = indexed_sources.read(config, db, client, vault)
            notes, token = current.notes, current.token
            indexed_experts = current.experts
            coverage = {
                **current.coverage,
                "index_state": "ready" if current.coverage["complete"] else "partial",
            }
        else:
            # status() may enqueue a rebuild; this display boundary never does.
            fingerprint = note_search_cache.fingerprint(config, db, vault)
            if generation["state"] != "ready" or generation["fingerprint"] != fingerprint:
                return Snapshot(root, {}, "unavailable", {**empty, "index_state": "stale"}, {})
            scope = for_vault(root, vault)
            notes = {}
            for number, row in enumerate(
                index.execute("SELECT path,metadata FROM notes ORDER BY path")
            ):
                if number >= MAX_NOTES:
                    raise IntegrityError("note cloud metadata limit")
                value = json.loads(row["metadata"])
                value["file_key"] = tuple(value["file_key"]) if value["file_key"] else None
                note = note_index.Note(**value)
                if note.text or note.relative_path != row["path"] or not scope.allows(row["path"]):
                    raise IntegrityError("note cloud index invalid")
                notes[row["path"]] = note
            token = fingerprint
            indexed_experts = {}
            if index.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='indexed_experts'"
            ).fetchone():
                indexed_experts = {
                    row[0]: (row[1], row[2])
                    for row in index.execute("SELECT id,version,path FROM indexed_experts")
                }
            coverage = {
                "scope": "indexed_sources",
                "complete": True,
                "index_state": "ready",
                "unavailable_count": 0,
                "unavailable": [],
            }
        if len(notes) > MAX_NOTES:
            raise IntegrityError("note cloud metadata limit")
        notes, coverage = cloud_visibility.admit(config, db, client, vault_id, notes, coverage)
        experts, pending_experts, expert_token = cloud_experts.read(
            config, db, client, vault_id, root, notes, indexed_experts
        )
        if len({identity(vault_id, note) for note in notes.values()}) != len(notes):
            raise IntegrityError("note cloud duplicate note identity")
        allowed = for_vault(root, vault)
        inaccessible = [
            entry
            for entry in coverage.get("unavailable", [])
            if entry.get("path") and not allowed.allows(entry["path"])
        ]
        if inaccessible:
            coverage = {
                **coverage,
                "unavailable": [
                    entry for entry in coverage.get("unavailable", []) if entry not in inaccessible
                ],
                "unavailable_count": max(
                    0, coverage.get("unavailable_count", 0) - len(inaccessible)
                ),
            }
        coverage = {
            **coverage,
            "complete": bool(coverage["complete"] and not pending_experts),
            "indexed_expert_entries": len(experts),
            "excluded_expert_entries": pending_experts,
            "expert_entry_scope": "authorized_indexed_entries",
        }
        epoch = db.execute(
            "SELECT revision FROM graph_permission_epochs WHERE client_id=? AND vault_id=?",
            (client, vault_id),
        ).fetchone()
        token = digest(
            canonical([token, epoch[0] if epoch else None, expert_token, coverage]).encode()
        )
        return Snapshot(root, notes, token, coverage, experts)


def verify(
    config: Config, db: sqlite3.Connection, client: str, vault: str, before: Snapshot
) -> None:
    after = read(config, db, client, vault)
    if (
        before.token != after.token
        or before.notes != after.notes
        or before.experts != after.experts
    ):
        raise note_search_cache.IndexUnavailable("note_index_update_required")


def wire(vault: str, note: note_index.Note, *, kind: str = "note") -> dict[str, Any]:
    return {
        "id": identity(vault, note),
        "kind": kind,
        "label": note.title,
        "path": note.relative_path,
        "sha256": note.sha256,
        "note_id": note.identity,
    }
