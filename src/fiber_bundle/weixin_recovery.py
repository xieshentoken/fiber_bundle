"""Append-only attachment recovery, leaving original poll replay identity intact."""

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from . import database, weixin_inbox
from .config import Config, IntegrityError, InvalidInput
from .objects import digest
from .weixin_events import Binding, prepare, requirements


def exists(db: sqlite3.Connection) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wx_recovered_media'"
        ).fetchone()
        is not None
    )


def initialize(config: Config, binding: Binding) -> None:
    with weixin_inbox.connection(config, binding) as db, database.transaction(db):
        weixin_inbox.bound(db, binding)
        db.execute(
            "CREATE TABLE IF NOT EXISTS wx_recovered_media("
            "account_id TEXT NOT NULL,event_id TEXT NOT NULL,item_key TEXT NOT NULL,"
            "raw_sha256 TEXT NOT NULL,data BLOB NOT NULL,sha256 TEXT NOT NULL,"
            "PRIMARY KEY(account_id,event_id,item_key),"
            "FOREIGN KEY(account_id,event_id) REFERENCES events(account_id,event_id)) STRICT"
        )


def load(
    db: sqlite3.Connection, binding: Binding, job_id: str
) -> tuple[sqlite3.Row, dict[str, bytes], set[str], str]:
    row = db.execute(
        "SELECT * FROM events WHERE account_id=? AND job_id=?", (binding.account_id, job_id)
    ).fetchone()
    if row is None:
        raise InvalidInput("unknown recovery event")
    raw = bytes(row["raw"])
    if digest(raw) != row["raw_sha256"]:
        raise IntegrityError("recovery event digest mismatch")
    message = json.loads(raw)
    media: dict[str, bytes] = {}
    for item in db.execute(
        "SELECT * FROM media WHERE account_id=? AND event_id=?",
        (binding.account_id, row["event_id"]),
    ):
        value = bytes(item["data"])
        if digest(value) != item["sha256"]:
            raise IntegrityError("original attachment digest mismatch")
        media[item["item_key"]] = value
    response = {"get_updates_buf": "", "msgs": [message]}
    _, _, events, _ = prepare(binding, response, {row["event_id"]: media})
    if len(events) != 1 or events[0].state != row["state"]:
        raise IntegrityError("original recovery event state mismatch")
    if exists(db):
        for item in db.execute(
            "SELECT * FROM wx_recovered_media WHERE account_id=? AND event_id=?",
            (binding.account_id, row["event_id"]),
        ):
            value = bytes(item["data"])
            if (
                item["raw_sha256"] != row["raw_sha256"]
                or digest(value) != item["sha256"]
                or item["item_key"] in media
            ):
                raise IntegrityError("recovered attachment identity mismatch")
            media[item["item_key"]] = value
    _, _, events, _ = prepare(binding, response, {row["event_id"]: media})
    required, _ = requirements(message["item_list"])
    return row, media, required, events[0].state


def current_state(db: sqlite3.Connection, binding: Binding, job_id: str, original: str) -> str:
    """Used by batch views; never rewrite the original receipt or its replay response."""
    if not exists(db):
        return original
    return load(db, binding, job_id)[3]


def view(config: Config, binding: Binding, job_id: str) -> dict[str, Any]:
    with weixin_inbox.connection(config, binding) as db:
        weixin_inbox.bound(db, binding)
        row, media, required, state = load(db, binding, job_id)
        return {
            "job_id": job_id,
            "raw_sha256": row["raw_sha256"],
            "state": state,
            "missing_items": sorted(required - media.keys()),
            "dispatched": False,
        }


def ensure_active(db: sqlite3.Connection, binding: Binding, event_id: str) -> None:
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='wx_batch_members'").fetchone():
        cancelled = db.execute(
            "SELECT 1 FROM wx_batch_members m JOIN wx_batches b ON b.id=m.batch_id "
            "WHERE m.account_id=? AND m.event_id=? AND b.state='cancelled'",
            (binding.account_id, event_id),
        ).fetchone()
        if cancelled:
            raise InvalidInput("cancelled batch cannot accept recovery")


def complete(
    config: Config,
    binding: Binding,
    job_id: str,
    expected_raw_sha256: str,
    attachments: dict[str, bytes],
    *,
    guard: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Trusted downloader supplies bytes for exact original item keys; no network here.

    Partial recovery is allowed. Same bytes are idempotent, replacement is refused.
    Bytes and their event binding commit together; any failure rolls back the whole call.
    """
    if not isinstance(attachments, dict) or not attachments:
        raise InvalidInput("recovery requires attachments")
    with weixin_inbox.connection(config, binding) as db, database.transaction(db):
        weixin_inbox.bound(db, binding)
        if guard is not None:
            guard()
        if not exists(db):
            raise InvalidInput("recovery must be initialized")
        row, media, required, _ = load(db, binding, job_id)
        if row["raw_sha256"] != expected_raw_sha256:
            raise InvalidInput("recovery event binding mismatch")
        ensure_active(db, binding, row["event_id"])
        if not set(attachments) <= required:
            raise InvalidInput("unexpected recovery item")
        for key, data in attachments.items():
            if key in media and media[key] != data:
                raise IntegrityError("attachment replacement refused")
        merged = {**media, **attachments}
        _, _, events, _ = prepare(
            binding,
            {"get_updates_buf": "", "msgs": [json.loads(row["raw"])]},
            {row["event_id"]: merged},
        )
        for key, data in attachments.items():
            if key not in media:
                db.execute(
                    "INSERT INTO wx_recovered_media VALUES(?,?,?,?,?,?)",
                    (
                        binding.account_id,
                        row["event_id"],
                        key,
                        row["raw_sha256"],
                        data,
                        digest(data),
                    ),
                )
        return {
            "job_id": job_id,
            "raw_sha256": row["raw_sha256"],
            "state": events[0].state,
            "missing_items": sorted(required - merged.keys()),
            "dispatched": False,
        }
