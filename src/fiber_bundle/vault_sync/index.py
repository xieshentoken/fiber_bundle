"""Replace only changed chunks in a private local SQLite copy; publish atomically."""

import json
import os
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict
from itertools import chain
from threading import Event
from uuid import NAMESPACE_URL, uuid4, uuid5

from .. import auth, database, note_index, note_inventory, note_search_jobs, vaults
from .. import note_search_cache as cache
from ..config import Config, IntegrityError, InvalidInput, make_directory, sync_directory
from ..executor_container import run_lock
from ..index_read_lease import Lease
from ..inputs import canonical
from ..search_matching import fold
from ..storage_versions import stamp
from . import (
    index_state,
    index_storage,
    progress,
    reconcile,
    scanner,
    scope_guard,
    store,
    visibility,
)

BATCH = 50


def update(
    config: Config,
    client: str,
    identity: str,
    stop: Event | None = None,
    *,
    cancelled: Callable[[sqlite3.Connection], bool] | None = None,
) -> dict[str, object]:
    with run_lock(config, str(uuid5(NAMESPACE_URL, "fiber-note-index:" + identity))):
        return _update(config, client, identity, stop, cancelled)


def _update(
    config: Config,
    client: str,
    identity: str,
    stop: Event | None,
    cancelled: Callable[[sqlite3.Connection], bool] | None,
) -> dict[str, object]:
    row = store.library(config, identity)
    if row is not None:
        scope_guard.current(config, row)
        row = store.library(config, identity)
    status = store.status(config, identity)
    if row is None or status is None or not row["baseline"] or status["active_pending"]:
        raise cache.IndexUnavailable("note_index_update_required")
    with database.connect(config.state_root) as main:
        vault = auth.grant(main, client, identity, "query")
        root = vaults.checked_vault(config, vault)
        proof_before = visibility.read(config, main, vault)
        visibility_before = proof_before.token
    with database.locked(config):
        job_id = note_search_jobs.record(config, identity, "building", reason="source_changed")
    with store.connection(config) as side:
        assert side is not None
        files = {
            r["path"]: dict(r)
            for r in side.execute("SELECT * FROM files WHERE vault=?", (identity,))
        }
        issues = {
            r["path"]: dict(r)
            for r in side.execute("SELECT * FROM issues WHERE vault=?", (identity,))
        }
        retry = {path for path, issue in issues.items() if issue["next_check"] <= time.time()}
    known = {p: (json.loads(v["signature"])[2], v["size"]) for p, v in files.items()}
    observations = {
        p: canonical(json.loads(v["signature"])[:8])
        for p, v in files.items()
        if p.lower().endswith(".md") and not v["directory"]
    }
    destination = cache.location(config, identity)
    make_directory(destination.parent)
    temporary = destination.parent / (str(uuid4()) + ".building")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    index = sqlite3.connect(temporary, isolation_level=None)
    index.row_factory = sqlite3.Row
    try:
        with cache.connection(config, identity) as prior:
            if prior is not None:
                prior.backup(index)
            else:
                index.executescript(cache.SCHEMA)
                index.execute("PRAGMA user_version=1")
        index.execute("PRAGMA synchronous=EXTRA")
        index.execute("PRAGMA max_page_count=" + str(cache.MAX_INDEX_BYTES // 4096))
        index.execute("CREATE TABLE IF NOT EXISTS sync_metadata(visibility TEXT NOT NULL)")
        proof_previous = visibility.previous(index)
        if index.execute("SELECT visibility FROM sync_metadata").fetchone() is None:
            proof_previous = None
        # Remove only paths whose visibility changed; a missing/old proof requires one
        # bounded readmission. Old live bytes remain intact until this slice is validated.
        invalidated = visibility.changed(proof_previous, proof_before, set(observations))
        for path in invalidated:
            for table in ("notes", "observations", "chunks"):
                index.execute("DELETE FROM " + table + " WHERE path=?", (path,))
        if invalidated:
            index_storage.reclaim(index)
        old = dict(index.execute("SELECT path,file_stat FROM observations"))
        changed = sorted(
            p for p, observed in observations.items() if old.get(p) != observed or p in retry
        )
        chosen = set(changed[:BATCH])
        removed = set(old) - set(observations)
        errors: dict[str, str] = {}
        loaded: dict[str, note_index.Note] = {}

        def loader(relative: str, maximum: int) -> note_index.Note:
            if stop is not None and stop.is_set():
                raise RuntimeError("vault_sync_stopping")
            if relative in errors:
                raise IntegrityError(errors[relative])
            if relative not in loaded:
                loaded[relative] = reconcile.current(config, client, identity, relative)
            return loaded[relative]

        def excluded(path: str) -> bool:
            return not any(path == p or p.startswith(path + "/") for p in chosen)

        notes: list[note_index.Note] = []
        if chosen:
            with database.connect(config.state_root) as main:
                vault = auth.grant(main, client, identity, "query")
                # The inventory decides visibility before any body is read. Reconcile
                # working identities first, then take a fresh, version-guarded projection.
                note_inventory.read(
                    config,
                    main,
                    vault,
                    limits=note_index.Limits(total_bytes=cache.MAX_LIBRARY_BYTES),
                    note_loader=loader,
                    exclude_path=excluded,
                    known_entries=known,
                    selected_paths=chosen,
                    note_errors=errors,
                    registered=False,
                )
        with Lease(config, identity) as lease, database.connect(config.state_root) as main:
            vault = auth.grant(main, client, identity, "query")
            if chosen:
                # Do not re-read failed files or change bindings inside this read lease.
                def current_note(relative: str, maximum: int) -> note_index.Note:
                    if relative not in loaded:
                        raise IntegrityError(errors.get(relative, "vault_sync_source_unavailable"))
                    return loaded[relative]

                inventory = lease.read(
                    lambda: note_inventory.read(
                        config,
                        main,
                        vault,
                        limits=note_index.Limits(total_bytes=cache.MAX_LIBRARY_BYTES),
                        note_loader=current_note,
                        exclude_path=excluded,
                        known_entries=known,
                        selected_paths=chosen,
                        note_errors=errors,
                    )
                )
                notes = inventory.notes
                for note in notes:
                    errors.pop(note.relative_path, None)
            expected = stamp(config, identity)
            proof_after = visibility.read(config, main, vault)
            after = proof_after.token
        # Rebinding one physical file updates that path's proof, not the whole Vault.
        invalidated_after = (
            visibility.changed(proof_before, proof_after, set(observations)) - chosen
        )
        for path in invalidated_after:
            for table in ("notes", "observations", "chunks"):
                index.execute("DELETE FROM " + table + " WHERE path=?", (path,))
        changed = sorted(set(changed) | invalidated_after)
        with database.transaction(index):
            for path in chosen | removed:
                index.execute("DELETE FROM notes WHERE path=?", (path,))
                index.execute("DELETE FROM observations WHERE path=?", (path,))
                index.execute("DELETE FROM chunks WHERE path=?", (path,))
            if chosen or removed or invalidated_after:
                index_storage.reclaim(index)
            for path in chosen:
                index.execute("INSERT INTO observations VALUES(?,?)", (path, observations[path]))
            for note in notes:
                index.execute(
                    "INSERT INTO notes VALUES(?,?)",
                    (note.relative_path, canonical({**asdict(note), "text": ""})),
                )
                for line, text in chain(
                    note_index.passages(note.text), note_index.adjacent_passages(note.text)
                ):
                    index.execute(
                        "INSERT INTO chunks VALUES(?,?,?,?,?)",
                        (note.relative_path, line, text, fold(text), fold(note.title)),
                    )
            if index.execute("SELECT count(*) FROM chunks").fetchone()[0] > cache.MAX_CHUNKS:
                raise InvalidInput("note_index_chunk_limit")
            note_count = index.execute("SELECT count(*) FROM notes").fetchone()[0]
            chunk_count = index.execute("SELECT count(*) FROM chunks").fetchone()[0]
        # Reconciliation may intentionally advance a physical binding. Capture its final
        # visibility, then recheck every file actually read before publishing the copy.
        with database.locked(config), database.connect(config.state_root) as main:
            if (stop is not None and stop.is_set()) or (cancelled and cancelled(main)):
                raise InvalidInput("note_index_cancelled")
            vault = auth.grant(main, client, identity, "query")
            vaults.checked_vault(config, vault)
            if expected != stamp(config, identity) or after != index_state.visibility(
                config, main, vault
            ):
                raise IntegrityError("vault_sync_metadata_changed")
            for path in loaded:
                if scanner.signature((root / path).lstat()) != files[path]["signature"]:
                    raise IntegrityError("vault_sync_source_changed")
            # Unchanged paths keep their old entries. A concurrent event remains pending and
            # cannot be acknowledged by this generation's publication.
            with database.transaction(index):
                index.execute("DELETE FROM sync_metadata")
                index.execute("INSERT INTO sync_metadata VALUES(?)", (after,))
                visibility.save(index, proof_after)
                index.execute("DELETE FROM generation")
                from hashlib import sha256

                fingerprint = sha256(
                    canonical([identity, row["generation"], after]).encode()
                ).hexdigest()
                index.execute(
                    "INSERT INTO generation VALUES(?,'ready',?)", (fingerprint, time.time())
                )
            index.close()
            os.replace(temporary, destination)
            sync_directory(destination.parent)
            pending = len(changed) - len(chosen)
            attention = progress.complete(
                config,
                identity,
                row,
                files,
                issues,
                chosen,
                removed,
                errors,
                pending,
                {
                    "updated_notes": len(notes),
                    "reused_notes": max(0, note_count - len(notes)),
                    "notes": note_count,
                    "chunks": chunk_count,
                },
                job_id,
            )
            complete = pending == 0
            result: dict[str, object] = {
                "state": "building" if not complete else "partial" if attention else "ready",
                "updated_notes": len(notes),
                "pending_notes": len(changed) - len(chosen),
                "needs_attention": attention,
                "notes": note_count,
                "chunks": chunk_count,
                "reused_notes": max(0, note_count - len(notes)),
                "index_mode": "persistent_chunks",
                "model_calls": 0,
                "vault_id": identity,
                "visibility_changed": visibility_before != after,
            }
            return result
    finally:
        index.close()
        temporary.unlink(missing_ok=True)
