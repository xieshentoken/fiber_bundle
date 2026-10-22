"""Optional Vault-scoped disk index; permissions and current files remain authoritative."""

import hashlib
import json
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import note_identity_store, note_index, note_inventory, note_publications, vaults
from .config import Config, IntegrityError, InvalidInput, contained
from .inputs import canonical
from .search_matching import fold, terms

MAX_LIBRARY_BYTES = 256 * 1024 * 1024
MAX_INDEX_BYTES = 256 * 1024 * 1024
MAX_CHUNKS = 500_000
MAX_MATCHES = 20_000
SCHEMA = """
CREATE TABLE generation (fingerprint TEXT NOT NULL, state TEXT NOT NULL,
 built_at REAL NOT NULL) STRICT;
CREATE TABLE notes (path TEXT PRIMARY KEY, metadata TEXT NOT NULL) STRICT;
CREATE TABLE observations (path TEXT PRIMARY KEY, file_stat TEXT NOT NULL) STRICT;
CREATE VIRTUAL TABLE chunks USING fts5(path UNINDEXED, line UNINDEXED, snippet UNINDEXED,
 body, title, tokenize='trigram case_sensitive 1');
"""


class IndexVersionError(IntegrityError):
    """The optional cache uses an unsupported version; never repair it on read."""


class IndexUnavailable(InvalidInput):
    """Fixed public condition, without paths, contents or SQLite error text."""

    def __init__(self, code: str):
        if code not in {"note_index_update_required", "note_index_match_limit"}:
            raise ValueError("invalid index condition")
        super().__init__(code)


def location(config: Config, vault_id: str) -> Path:
    from .note_changes import identifier

    identifier(vault_id)
    return contained(config.state_root, "note-search/" + vault_id + ".sqlite3")


def fingerprint(config: Config, db: sqlite3.Connection, vault: sqlite3.Row) -> str:
    """Metadata-only detection includes new files, deletion, moves and ledger visibility."""
    from .note_migration_store import guard

    guard(config, vault["id"])
    root = vaults.checked_vault(config, vault)
    h = hashlib.sha256()
    info = root.stat()
    h.update(canonical([info.st_dev, info.st_ino]).encode())
    from .vault_scope import entries, for_vault

    scope = for_vault(root, vault)
    h.update(canonical(scope.wire()).encode())
    from .organization.packages import roots as expert_roots

    registered_experts = expert_roots(config, vault["id"])
    if registered_experts:
        h.update(canonical(["organization-expert-roots", sorted(registered_experts)]).encode())
    for p, i in entries(
        root,
        scope,
        hidden=True,
        batch=True,
        file_filter=lambda p: (
            p.name.lower().endswith(".md")
            or p.name in {note_publications.MARKER, ".fiber-analysis.json"}
        ),
    ):
        if stat.S_ISDIR(i.st_mode):
            continue
        if not scope.allows(p.relative_to(root).as_posix()):
            continue
        if not p.name.lower().endswith(".md") and p.name not in {
            note_publications.MARKER,
            ".fiber-analysis.json",
        }:
            continue
        if p.name.startswith(".") and p.name not in {
            note_publications.MARKER,
            ".fiber-analysis.json",
        }:
            continue
        h.update(
            canonical(
                [
                    p.relative_to(root).as_posix(),
                    i.st_dev,
                    i.st_ino,
                    i.st_mode,
                    i.st_nlink,
                    i.st_size,
                    i.st_mtime_ns,
                    i.st_ctime_ns,
                ]
            ).encode()
        )
    for sql in (
        "SELECT * FROM publication_locations WHERE vault_id=? ORDER BY kind,publication_id",
        (
            "SELECT p.* FROM article_publications p LEFT JOIN publication_locations l "
            "ON l.kind='article' AND l.publication_id=p.id WHERE "
            "COALESCE(l.vault_id,p.vault_id)=? ORDER BY p.id"
        ),
        "SELECT p.* FROM document_publications p JOIN execution_runs r ON r.id=p.run_id "
        "WHERE r.vault_id=? ORDER BY p.run_id",
        "SELECT p.* FROM media_publications p JOIN media_tasks m ON m.id=p.job_id "
        "WHERE m.vault_id=? ORDER BY p.job_id",
    ):
        for row in db.execute(sql, (vault["id"],)):
            h.update(canonical(dict(row)).encode())
    for record in note_identity_store.records(config, vault["id"]):
        h.update(canonical(asdict(record)).encode())
    # Private analysis publication state is held in its own ledger.
    from .document_creation_save import inventory_paths

    h.update(canonical([sorted(x) for x in inventory_paths(db, vault["id"])]).encode())
    return h.hexdigest()


@contextmanager
def connection(config: Config, vault_id: str) -> Iterator[sqlite3.Connection | None]:
    path = location(config, vault_id)
    if not path.exists():
        yield None
        return
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_INDEX_BYTES:
            raise IntegrityError("note search index invalid")
    finally:
        os.close(fd)
    cache = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, isolation_level=None)
    cache.row_factory = sqlite3.Row
    try:
        if cache.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise IndexVersionError("note search index version invalid")
        yield cache
    finally:
        cache.close()


def status(config: Config, db: sqlite3.Connection, vault: sqlite3.Row) -> dict[str, Any]:
    with connection(config, vault["id"]) as cache:
        if cache is None:
            return {"state": "absent", "index_mode": "current_read_only_snapshot"}
        generation = cache.execute("SELECT * FROM generation").fetchone()
        if generation is None:
            raise IntegrityError("note search generation missing")
        current = fingerprint(config, db, vault)
        state = (
            "ready"
            if generation["state"] == "ready" and current == generation["fingerprint"]
            else "stale"
        )
        if state == "stale":
            from .display_observation import pending_indexes
            from .note_search_jobs import changed

            pending = pending_indexes.get()
            if pending is None:
                changed(config, vault["id"])
            else:
                pending.add(vault["id"])
        return {
            "state": state,
            "index_mode": "persistent_chunks",
            "notes": cache.execute("SELECT count(*) FROM notes").fetchone()[0],
            "built_at": generation["built_at"],
        }


def candidates(
    config: Config, db: sqlite3.Connection, vault: sqlite3.Row, query: str
) -> tuple[note_inventory.Inventory, list[tuple[tuple[int, int, str], note_index.Note]]] | None:
    patterns = terms(query)
    with connection(config, vault["id"]) as cache:
        if cache is None:
            return None
        generation = cache.execute("SELECT * FROM generation").fetchone()
        if (
            generation is None
            or generation["state"] != "ready"
            or generation["fingerprint"] != fingerprint(config, db, vault)
        ):
            from .note_search_jobs import changed

            changed(config, vault["id"])
            raise IndexUnavailable("note_index_update_required")
        words = list(dict.fromkeys(fold(query).split()))
        clauses = []
        parameters: list[Any] = []
        large = [w for w in words if len(w) >= 3]
        if large:
            clauses.append("chunks MATCH ?")
            parameters.append(
                " AND ".join(
                    '(body : "'
                    + w.replace('"', '""')
                    + '" OR title : "'
                    + w.replace('"', '""')
                    + '")'
                    for w in large
                )
            )
        for word in words:
            if len(word) < 3:
                clauses.append("(instr(body,?)>0 OR instr(title,?)>0)")
                parameters.extend([word, word])
        sql = (
            "SELECT path,line,snippet FROM chunks WHERE "
            + " AND ".join(clauses)
            + " ORDER BY rowid LIMIT ?"
        )
        parameters.append(MAX_MATCHES + 1)
        best: dict[str, tuple[int, int, str, bool]] = {}
        import re

        for number, row in enumerate(cache.execute(sql, parameters), 1):
            if number > MAX_MATCHES:
                raise IndexUnavailable("note_index_match_limit")
            raw = cache.execute(
                "SELECT metadata FROM notes WHERE path=?", (row["path"],)
            ).fetchone()
            if raw is None:
                raise IntegrityError("note index chunk has no source")
            metadata = json.loads(raw[0])
            title = fold(metadata["title"])
            passage = row["snippet"]
            body = fold(passage)
            if not all(p.search(title) or p.search(body) for p in patterns):
                continue
            score = sum(3 * bool(p.search(title)) + bool(p.search(body)) for p in patterns)
            heading = re.match(r"^\s{0,3}#{1,6}(?:\s|$)", passage) is not None
            old = best.get(row["path"])
            if (
                old is None
                or score > old[0]
                or (score == old[0] and old[3] and not heading)
                or (score == old[0] and heading == old[3] and len(passage) < len(old[2]))
            ):
                best[row["path"]] = (score, int(row["line"]), passage, heading)
        notes = []
        for relative, match in best.items():
            raw = cache.execute("SELECT metadata FROM notes WHERE path=?", (relative,)).fetchone()
            value = json.loads(raw[0])
            value["file_key"] = tuple(value["file_key"]) if value["file_key"] is not None else None
            notes.append((match[:3], note_index.Note(**value)))
    publications = {
        p["id"]: p
        for p in db.execute(
            (
                "SELECT p.* FROM article_publications p LEFT JOIN publication_locations l "
                "ON l.kind='article' AND l.publication_id=p.id WHERE "
                "COALESCE(l.vault_id,p.vault_id)=? AND p.state='published'"
            ),
            (vault["id"],),
        )
    }
    from .publication_locations import binding as location_binding

    inventory = note_inventory.Inventory(
        vaults.checked_vault(config, vault),
        [n for _, n in notes],
        publications,
        {pid: location_binding(db, p) for pid, p in publications.items()},
        {
            n.publication_id: n.publication_path
            for _, n in notes
            if n.publication_id is not None and n.publication_path is not None
        },
    )
    return inventory, notes
