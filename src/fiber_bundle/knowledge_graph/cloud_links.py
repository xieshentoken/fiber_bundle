"""Literal relationships reconstructed from existing index passages and MOC ledgers."""

import re
import sqlite3
from typing import Any

from .. import (
    note_search_cache,
    source_import_markdown,
)
from ..config import Config, IntegrityError
from . import cloud_memberships, projection
from .cloud_cache import reconstruct
from .cloud_sources import Snapshot, identity

MAX_TEXT_BYTES = 64 * 1024 * 1024
MAX_CHUNKS = 500_000
MAX_RELATIONS = 100_000


_text = reconstruct


def read(
    config: Config,
    main: sqlite3.Connection,
    vault: str,
    snapshot: Snapshot,
    *,
    path_ids: dict[str, str] | None = None,
    moc_ids: dict[str, str] | None = None,
    observed: dict[str, cloud_memberships.Evidence] | None = None,
) -> tuple[dict[str, str], list[tuple[str, str, str]], dict[str, Any]]:
    if not snapshot.notes:
        return (
            {},
            [],
            {
                "links_complete": bool(snapshot.coverage["complete"]),
                "link_source": "indexed_text",
                "link_unavailable_notes": 0,
                "moc_membership_unavailable": 0,
            },
        )
    paths = (
        path_ids
        if path_ids is not None
        else {path: identity(vault, note) for path, note in snapshot.notes.items()}
    )
    kinds: dict[str, str] = {}
    relations: set[tuple[str, str, str]] = set()
    incomplete = 0
    relation_limit = False
    used = 0
    chunks = 0

    def add(source: str, target: str, kind: str) -> None:
        nonlocal relation_limit
        value = (min(source, target), max(source, target), kind)
        if len(relations) < MAX_RELATIONS or value in relations:
            relations.add(value)
        else:
            relation_limit = True

    from .. import vault_save_policy

    moc_dir = vault_save_policy.get(config, main, vault)["settings"].get("moc_dir", "Map")
    processed: set[str] = set()

    def consume(path: str, rows: list[tuple[int, str]]) -> None:
        nonlocal incomplete, used
        if path in processed:
            # Existing index writers replace all of a note's rows together.
            # Interleaving invalidates that reconstruction proof.
            raise IntegrityError("note cloud index chunks invalid")
        processed.add(path)
        if used >= MAX_TEXT_BYTES:
            incomplete += 1
            return
        text = _text(rows)
        if text is None:
            incomplete += 1
            return
        used += len(text.encode())
        if used > MAX_TEXT_BYTES:
            incomplete += 1
            return
        source = paths[path]
        if observed is not None and moc_ids and path in moc_ids:
            observed[path] = cloud_memberships.observe(
                text, moc_ids[path], snapshot.notes[path], paths
            )
        if path.startswith(moc_dir + "/") and (
            re.search(r"(?im)^type:\s*moc\s*$", text)
            or re.search(r"(?i)\bmoc$", path.rsplit("/", 1)[-1][:-3])
        ):
            kinds[source] = "moc"
        for reference in source_import_markdown.references(text):
            target = projection.resolve(snapshot.notes[path], reference, paths)
            if target and target != source:
                add(source, target, "explicit")

    with note_search_cache.connection(config, vault) as index:
        if index is None:
            return kinds, [], {"links_complete": False, "link_source": "indexed_text"}
        # Both writers store a note's primary windows followed by adjacent
        # passages. Stream rowid once; retain at most one note's primary windows,
        # never the repeated adjacent rows or all source bodies in memory.
        current: str | None = None
        source_rows: list[tuple[int, str]] = []
        primary_done = False
        previous_line = 0
        limited = False
        for row in index.execute("SELECT path,line,snippet FROM chunks ORDER BY rowid"):
            if row["path"] not in snapshot.notes:
                continue
            chunks += 1
            if chunks > MAX_CHUNKS:
                limited = True
                break
            if row["path"] != current:
                if current is not None:
                    consume(current, source_rows)
                current, source_rows = row["path"], []
                previous_line, primary_done = 0, False
            if not primary_done:
                passage = row["snippet"]
                primary_done = "\n" in passage or "\r" in passage or row["line"] < previous_line
                if not primary_done:
                    source_rows.append((row["line"], passage))
                    previous_line = row["line"]
        if current is not None and not limited:
            consume(current, source_rows)
        for path in snapshot.notes.keys() - processed:
            if limited:
                incomplete += 1
            else:
                consume(path, [])  # Empty indexed notes have no primary passage.

    return (
        kinds,
        sorted(relations),
        {
            "links_complete": incomplete == 0 and not relation_limit,
            "link_source": "indexed_text",
            "link_unavailable_notes": incomplete,
            "link_byte_limit": MAX_TEXT_BYTES,
            "indexed_text_bytes": used,
            "link_relation_limit": MAX_RELATIONS,
            "link_relation_limit_reached": relation_limit,
        },
    )
