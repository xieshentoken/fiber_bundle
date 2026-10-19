"""Admin-only projection of locally bound Weixin batches."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fiber_bundle import auth, database, vaults, weixin_batches
from fiber_bundle.config import Config, IntegrityError, InvalidInput, contained
from fiber_bundle.weixin_events import Binding


@contextmanager
def journal(config: Config, *, write: bool = False) -> Iterator[sqlite3.Connection | None]:
    path = contained(config.state_root, "weixin-inbox.sqlite3")
    for suffix in ("-journal", "-wal", "-shm"):
        contained(config.state_root, "weixin-inbox.sqlite3" + suffix)
    if not path.exists():
        yield None
        return
    db = sqlite3.connect(path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys=ON")
        if db.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise IntegrityError("unsupported weixin inbox schema")
        yield db
    finally:
        db.close()


def binding_for(row: sqlite3.Row) -> Binding:
    try:
        data = json.loads(row["binding_json"])
        if not isinstance(data, dict) or set(data) != {
            "account_id",
            "owner_id",
            "client_id",
            "vault_id",
        }:
            raise ValueError
        binding = Binding(**data)
        binding.validate()
        if binding.account_id != row["account_id"]:
            raise ValueError
        return binding
    except (ValueError, TypeError, KeyError) as exc:
        raise IntegrityError("invalid weixin binding") from exc


def preview(db: sqlite3.Connection, batch_id: str) -> str:
    parts: list[str] = []
    for row in db.execute(
        "SELECT e.raw FROM wx_batch_members m JOIN events e "
        "ON e.account_id=m.account_id AND e.event_id=m.event_id "
        "WHERE m.batch_id=? ORDER BY m.ordinal LIMIT 3",
        (batch_id,),
    ):
        try:
            message = json.loads(row["raw"])
            for item in message.get("item_list", []):
                if item.get("type") == 1:
                    value = item.get("text_item", {}).get("text")
                    if isinstance(value, str):
                        parts.append(" ".join(value.split()))
        except (ValueError, TypeError, AttributeError):
            continue
    return " ".join(parts)[:120] or "原件已接收；无可预览文本。"


def pending(config: Config, *, include_selected: bool = False) -> list[dict[str, Any]]:
    with journal(config) as db:
        if db is None:
            return []
        if (
            db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wx_batches'"
            ).fetchone()
            is None
        ):
            return []
        rows = db.execute(
            "SELECT b.id,b.account_id,b.state,b.last_received,b.quiet_seconds,x.binding_json "
            "FROM wx_batches b JOIN bindings x ON x.account_id=b.account_id "
            "WHERE b.state IN ('buffering','frozen') "
            + (
                "OR b.state='selected' "
                if include_selected
                else "OR (b.state='selected' AND b.action='consult' AND b.question IS NULL) "
            )
            + "ORDER BY b.last_received DESC,b.id LIMIT 100"
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            binding = binding_for(row)
            view = weixin_batches.snapshot(db, binding, row["id"])
            from .batch_display import project

            details = project(config, db, binding, view)
            result.append(
                {
                    "id": row["id"],
                    "state": view["state"],
                    "count": len(view["members"]),
                    "received_at": row["last_received"],
                    "freeze_at": (
                        row["last_received"] + row["quiet_seconds"]
                        if view["state"] == "buffering"
                        else None
                    ),
                    "content_ready": view["content_ready"],
                    "needs_question": view["needs_question"],
                    "action": view["action"],
                    **details,
                }
            )
        return result


def choose(
    config: Config,
    main: sqlite3.Connection,
    batch_id: str,
    action: str,
    question: str | None = None,
) -> dict[str, Any]:
    if not batch_id or len(batch_id) > 400:
        raise InvalidInput("invalid batch ID")
    with journal(config, write=True) as db:
        if db is None:
            raise InvalidInput("unknown weixin batch")
        row = db.execute(
            "SELECT x.account_id,x.binding_json FROM wx_batches b "
            "JOIN bindings x ON x.account_id=b.account_id WHERE b.id=?",
            (batch_id,),
        ).fetchone()
        if row is None:
            raise InvalidInput("unknown weixin batch")
        binding = binding_for(row)
        if action != "cancel":
            vaults.checked_vault(
                config, auth.grant(main, binding.client_id, binding.vault_id, "add")
            )
        with database.transaction(db):
            result = weixin_batches.choose_in_transaction(
                db, binding, batch_id, action, question=question
            )
    from .channels.batch_intake import enqueue

    queued = enqueue(config, binding, batch_id, action)
    return {**result, "intake": queued} if queued is not None else result
