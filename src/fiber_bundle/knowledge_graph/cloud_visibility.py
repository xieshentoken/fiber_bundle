"""Metadata-only privacy gates beyond the vault's query capability."""

import sqlite3
from typing import Any

from .. import auth, document_creation_save, note_index, vault_merge_analysis
from ..agent_connections.visibility import scope
from ..config import Config


def path_allowed(db: sqlite3.Connection, client: str, vault: str, path: str, sha: str) -> bool:
    try:
        current = document_creation_save.search_source(db, client, vault, path, sha)
        if current is None:
            vault_merge_analysis.provenance(db, client, vault, path, sha)
        return True
    except auth.Denied:
        return False


def admit(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vault: str,
    notes: dict[str, note_index.Note],
    coverage: dict[str, Any],
) -> tuple[dict[str, note_index.Note], dict[str, Any]]:
    _, quality = scope(config, db, client, [vault])
    if quality != "all":
        # This page has no source-body/manifest validation phase. A quality-only
        # connection must use the existing verified retrieval projection rather
        # than obtaining unverified titles through a new metadata side channel.
        return {}, {
            "scope": "authorized_indexed_sources",
            "complete": False,
            "index_state": coverage["index_state"],
            "unavailable_count": 1,
            "unavailable": [{"reason": "quality_scope_requires_verified_projection"}],
        }
    analyses = {item["note_path"] for item in vault_merge_analysis.records(db, vault)}
    result = dict(notes)
    denied: set[str] = set()
    for path in analyses & notes.keys():
        if not path_allowed(db, client, vault, path, notes[path].sha256):
            result.pop(path)
            denied.add(path)
    if denied:
        # Match ordinary search: unauthorized analyses have no visible row,
        # title, path or count. Do not turn their omission into public coverage.
        coverage = {
            **coverage,
            "unavailable": [
                entry
                for entry in coverage.get("unavailable", [])
                if entry.get("path") not in denied
            ],
        }
    return result, {**coverage, "scope": "authorized_indexed_sources"}
