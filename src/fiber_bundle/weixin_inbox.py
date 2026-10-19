"""Pre-confirmation Weixin journal. No dispatch, model, publication or network access."""

import json
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from . import auth, database, vaults
from .config import Config, IntegrityError, InvalidInput, contained, sync_directory
from .objects import digest
from .weixin_events import Binding, prepare

SCHEMA = (
    "CREATE TABLE bindings(account_id TEXT PRIMARY KEY,binding_json TEXT NOT NULL,"
    "cursor TEXT NOT NULL,previous_cursor TEXT,last_digest TEXT,last_result TEXT) STRICT",
    "CREATE TABLE events(account_id TEXT NOT NULL REFERENCES bindings(account_id),"
    "event_id TEXT NOT NULL,job_id TEXT NOT NULL UNIQUE,raw BLOB NOT NULL,"
    "raw_sha256 TEXT NOT NULL,state TEXT NOT NULL "
    "CHECK(state IN ('pending','incomplete','unsupported')),"
    "received_at TEXT NOT NULL,PRIMARY KEY(account_id,event_id)) STRICT",
    "CREATE TABLE media(account_id TEXT NOT NULL,event_id TEXT NOT NULL,item_key TEXT NOT NULL,"
    "data BLOB NOT NULL,sha256 TEXT NOT NULL,PRIMARY KEY(account_id,event_id,item_key),"
    "FOREIGN KEY(account_id,event_id) REFERENCES events(account_id,event_id)) STRICT",
)


@contextmanager
def connection(
    config: Config, binding: Binding, *, create: bool = False, leased: bool = False
) -> Iterator[sqlite3.Connection]:
    binding.validate()
    with nullcontext() if leased else database.locked(config):
        main = database.connect(config.state_root)
        try:
            from .vault_relocation import guard

            row = auth.grant(main, binding.client_id, binding.vault_id, "add")
            if not (
                guard.metadata_only(binding.vault_id) and guard.operation(main, binding.vault_id)
            ):
                vaults.checked_vault(config, row)
        finally:
            main.close()
        path = contained(config.state_root, "weixin-inbox.sqlite3")
        for suffix in ("-journal", "-wal", "-shm"):
            contained(config.state_root, "weixin-inbox.sqlite3" + suffix)
        if create and not path.exists():
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            sync_directory(path.parent)
        db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=EXTRA")
            db.execute("PRAGMA fullfsync=ON")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if create and version == 0:
                with database.transaction(db):
                    for statement in SCHEMA:
                        db.execute(statement)
                    db.execute("PRAGMA user_version=1")
            elif version != 1:
                raise IntegrityError("unsupported weixin inbox schema")
            yield db
        finally:
            db.close()


def initialize(config: Config, binding: Binding) -> None:
    """Local trusted setup; channel payloads can never create or change this binding."""
    with connection(config, binding, create=True) as db, database.transaction(db):
        saved = db.execute(
            "SELECT binding_json FROM bindings WHERE account_id=?", (binding.account_id,)
        ).fetchone()
        value = json.dumps(asdict(binding), sort_keys=True)
        if saved is not None and saved[0] != value:
            raise InvalidInput("weixin binding change requires explicit rebind")
        db.execute(
            "INSERT OR IGNORE INTO bindings(account_id,binding_json,cursor) VALUES(?,?,'')",
            (binding.account_id, value),
        )


def bound(db: sqlite3.Connection, binding: Binding) -> sqlite3.Row:
    row = db.execute("SELECT * FROM bindings WHERE account_id=?", (binding.account_id,)).fetchone()
    if not isinstance(row, sqlite3.Row) or row["binding_json"] != json.dumps(
        asdict(binding), sort_keys=True
    ):
        raise InvalidInput("weixin binding mismatch")
    return row


def cursor(config: Config, binding: Binding) -> str:
    with connection(config, binding) as db:
        return str(bound(db, binding)["cursor"])


def receive(
    config: Config,
    binding: Binding,
    expected_cursor: str,
    response: object,
    media: dict[str, dict[str, bytes]] | None = None,
    *,
    guard: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Caller fetched using expected_cursor; advance only after durable event/byte commit.

    Return metadata only. Pending means captured, never approved for model processing.
    Missing/unknown content remains quarantined even when the poll cursor is acknowledged.
    """
    if not isinstance(expected_cursor, str) or len(expected_cursor) > 8192:
        raise InvalidInput("invalid expected cursor")
    new_cursor, fingerprint, events, ignored = prepare(
        binding, response, {} if media is None else media
    )
    from .vault_relocation.guard import receiving

    with receiving(binding.vault_id), connection(config, binding) as db, database.transaction(db):
        if guard is not None:
            guard()
        saved = bound(db, binding)
        replay = (
            saved["previous_cursor"] == expected_cursor
            and saved["cursor"] == new_cursor
            and saved["last_digest"] == fingerprint
        )
        if not replay and saved["cursor"] != expected_cursor:
            raise InvalidInput("stale weixin poll")
        results = []
        for event in events:
            old = db.execute(
                "SELECT * FROM events WHERE account_id=? AND event_id=?",
                (binding.account_id, event.event_id),
            ).fetchone()
            if old is not None:
                if bytes(old["raw"]) != event.raw or old["raw_sha256"] != digest(event.raw):
                    raise IntegrityError("weixin event identity conflict")
                stored_media = {
                    r["item_key"]: (bytes(r["data"]), r["sha256"])
                    for r in db.execute(
                        "SELECT * FROM media WHERE account_id=? AND event_id=?",
                        (binding.account_id, event.event_id),
                    )
                }
                # Replays do not overwrite or silently complete a previously missing attachment.
                if set(stored_media) != set(event.media) or any(
                    stored_media[k] != (v, digest(v)) for k, v in event.media.items()
                ):
                    raise IntegrityError("weixin attachment identity conflict")
                if old["state"] != event.state:
                    raise IntegrityError("weixin event state conflict")
                job_id = old["job_id"]
            else:
                if replay:
                    raise IntegrityError("weixin replay event missing")
                job_id = str(uuid4())
                db.execute(
                    "INSERT INTO events VALUES(?,?,?,?,?,?,?)",
                    (
                        binding.account_id,
                        event.event_id,
                        job_id,
                        event.raw,
                        digest(event.raw),
                        event.state,
                        datetime.now(UTC).isoformat(),
                    ),
                )
                for key, data in event.media.items():
                    db.execute(
                        "INSERT INTO media VALUES(?,?,?,?,?)",
                        (binding.account_id, event.event_id, key, data, digest(data)),
                    )
            results.append({"job_id": job_id, "state": event.state})
        result = {"events": results, "ignored_messages": ignored, "dispatched": False}
        if replay:
            if json.loads(saved["last_result"]) != result:
                raise IntegrityError("weixin replay result mismatch")
        else:
            db.execute(
                "UPDATE bindings SET previous_cursor=?,cursor=?,last_digest=?,last_result=? "
                "WHERE account_id=?",
                (expected_cursor, new_cursor, fingerprint, json.dumps(result), binding.account_id),
            )
        return result
