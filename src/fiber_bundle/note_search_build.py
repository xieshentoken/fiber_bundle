"""Explicit bounded disk-index construction, with text I/O outside the storage lease."""

import os
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict
from itertools import chain
from uuid import NAMESPACE_URL, uuid4, uuid5

from . import (
    auth,
    database,
    note_index,
    note_inventory,
    note_search_jobs,
    note_search_reuse,
    vaults,
)
from . import note_search_cache as cache
from .config import Config, IntegrityError, InvalidInput, make_directory, sync_directory
from .executor_container import run_lock
from .inputs import canonical
from .search_matching import fold


def build(
    config: Config,
    client_id: str,
    vault_id: str,
    *,
    cancelled: Callable[[sqlite3.Connection], bool] | None = None,
) -> dict[str, object]:
    # The local command owns one Vault index; unrelated query/import locks stay free.
    with run_lock(config, str(uuid5(NAMESPACE_URL, "fiber-note-index:" + vault_id))):
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                vault = auth.grant(db, client_id, vault_id, "query")
                destination = cache.location(config, vault_id)
                make_directory(destination.parent)
                before = cache.fingerprint(config, db, vault)
                epoch = db.execute(
                    "SELECT revision FROM graph_permission_epochs WHERE client_id=? AND vault_id=?",
                    (client_id, vault_id),
                ).fetchone()[0]
            finally:
                db.close()
        with database.locked(config):
            job_id = note_search_jobs.record(config, vault_id, "building", reason="explicit_update")
        # Incomplete/crashed build bytes are preserved as staging, not a live index DB.
        temporary = destination.parent / (str(uuid4()) + ".building")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        index = sqlite3.connect(temporary, isolation_level=None)
        db = database.connect(config.state_root)
        count = 0
        reused: set[str] = set()
        prior = cache.connection(config, vault_id)
        old = prior.__enter__()
        root = vaults.checked_vault(config, vault)
        try:
            index.executescript(cache.SCHEMA)
            index.execute("PRAGMA user_version=1")
            index.execute("PRAGMA max_page_count=" + str(cache.MAX_INDEX_BYTES // 4096))
            index.execute("PRAGMA synchronous=EXTRA")

            def consume(note: note_index.Note) -> None:
                nonlocal count
                if cancelled is not None and cancelled(db):
                    raise InvalidInput("note_index_cancelled")
                index.execute(
                    "INSERT INTO notes VALUES(?,?)",
                    (note.relative_path, canonical({**asdict(note), "text": ""})),
                )
                index.execute(
                    "INSERT INTO observations VALUES(?,?)",
                    (
                        note.relative_path,
                        note_search_reuse.observation(
                            root, note.relative_path, note_index.Limits().note_bytes
                        ),
                    ),
                )
                if note.relative_path in reused:
                    assert old is not None
                    for row in old.execute(
                        "SELECT path,line,snippet,body,title FROM chunks "
                        "WHERE path=? ORDER BY rowid",
                        (note.relative_path,),
                    ):
                        count += 1
                        if count > cache.MAX_CHUNKS:
                            raise InvalidInput("note_index_chunk_limit")
                        index.execute("INSERT INTO chunks VALUES(?,?,?,?,?)", tuple(row))
                    return
                for line, passage in chain(
                    note_index.passages(note.text), note_index.adjacent_passages(note.text)
                ):
                    count += 1
                    if count > cache.MAX_CHUNKS:
                        raise InvalidInput("note_index_chunk_limit")
                    index.execute(
                        "INSERT INTO chunks VALUES(?,?,?,?,?)",
                        (note.relative_path, line, passage, fold(passage), fold(note.title)),
                    )

            with database.transaction(index):
                inventory = note_inventory.read(
                    config,
                    db,
                    vault,
                    limits=note_index.Limits(total_bytes=cache.MAX_LIBRARY_BYTES),
                    on_note=consume,
                    note_loader=lambda relative, maximum: note_search_reuse.load(
                        root, relative, maximum, old, reused
                    ),
                )
                for note in inventory.notes:
                    index.execute(
                        "UPDATE notes SET metadata=? WHERE path=?",
                        (canonical(asdict(note)), note.relative_path),
                    )
                index.execute(
                    "INSERT INTO generation VALUES(?,?,?)", (before, "ready", time.time())
                )
            # Validate source/permission again before atomically making this generation visible.
            with database.locked(config):
                if cancelled is not None and cancelled(db):
                    raise InvalidInput("note_index_cancelled")
                current = auth.grant(db, client_id, vault_id, "query")
                now = db.execute(
                    "SELECT revision FROM graph_permission_epochs WHERE client_id=? AND vault_id=?",
                    (client_id, vault_id),
                ).fetchone()[0]
                if now != epoch or cache.fingerprint(config, db, current) != before:
                    raise IntegrityError("note index source or permission changed")
                index.close()
                os.replace(temporary, destination)
                sync_directory(destination.parent)
            result: dict[str, object] = {
                "state": "ready",
                "vault_id": vault_id,
                "notes": len(inventory.notes),
                "chunks": count,
                "reused_notes": len(reused),
                "updated_notes": len(inventory.notes) - len(reused),
                "index_mode": "persistent_chunks",
                "model_calls": 0,
            }
            with database.locked(config):
                note_search_jobs.record(
                    config,
                    vault_id,
                    "ready",
                    reason="completed",
                    expected_id=job_id,
                    result={
                        key: result[key]
                        for key in ("notes", "chunks", "reused_notes", "updated_notes")
                    },
                )
            from .organization.jobs import notify
            from .organization.jobs import policy as organization_policy

            with database.locked(config):
                target = (
                    "全库"
                    if organization_policy(config, vault_id)["settings"]["full_review"]
                    else "Inbox"
                )
                notify(config, vault_id, "changed", target)
            return result
        except BaseException:
            with database.locked(config):
                note_search_jobs.record(
                    config, vault_id, "failed", reason="index_update_failed", expected_id=job_id
                )
            raise
        finally:
            prior.__exit__(None, None, None)
            index.close()
            db.close()
            temporary.unlink(missing_ok=True)
