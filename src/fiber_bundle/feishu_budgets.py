"""Explicit immutable grants and durable consumption in the existing ingress journal."""

import sqlite3
from dataclasses import asdict
from typing import Literal

from . import database, weixin_inbox
from .config import Config, InvalidInput
from .inputs import canonical
from .weixin_events import Binding

Kind = Literal["download", "store", "model", "outbound"]


def initialize(config: Config, binding: Binding) -> None:
    with weixin_inbox.connection(config, binding) as db, database.transaction(db):
        db.execute(
            "CREATE TABLE IF NOT EXISTS fiber_feishu_authorizations("
            "account_id TEXT NOT NULL,authorization_id TEXT NOT NULL,binding_json TEXT NOT NULL,"
            "download_limit INTEGER NOT NULL,store_limit INTEGER NOT NULL,"
            "model_limit INTEGER NOT NULL DEFAULT 0,outbound_limit INTEGER NOT NULL DEFAULT 0,"
            "PRIMARY KEY(account_id,authorization_id)) STRICT"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS fiber_feishu_consumption("
            "account_id TEXT NOT NULL,authorization_id TEXT NOT NULL,kind TEXT NOT NULL,"
            "target TEXT NOT NULL,PRIMARY KEY(account_id,authorization_id,kind,target),"
            "FOREIGN KEY(account_id,authorization_id) REFERENCES "
            "fiber_feishu_authorizations(account_id,authorization_id)) STRICT"
        )


def authorize(
    config: Config,
    binding: Binding,
    identity: str | None,
    downloads: int,
    stores: int,
    models: int = 0,
    outbound: int = 0,
) -> None:
    if any(type(v) is not int or not 0 <= v <= 100 for v in (downloads, stores, models, outbound)):
        raise InvalidInput("invalid Feishu authorization limits")
    initialize(config, binding)
    if identity is None:
        if downloads or stores or models or outbound:
            raise InvalidInput("nonzero Feishu quota requires a new explicit authorization_id")
        return
    with weixin_inbox.connection(config, binding) as db, database.transaction(db):
        scope = canonical(asdict(binding))
        old = db.execute(
            "SELECT * FROM fiber_feishu_authorizations WHERE account_id=? AND authorization_id=?",
            (binding.account_id, identity),
        ).fetchone()
        if old is not None:
            if (
                old["binding_json"],
                old["download_limit"],
                old["store_limit"],
                old["model_limit"],
                old["outbound_limit"],
            ) != (
                scope,
                downloads,
                stores,
                models,
                outbound,
            ):
                raise InvalidInput("Feishu authorization cannot be changed or replenished")
        else:
            db.execute(
                "INSERT INTO fiber_feishu_authorizations"
                "(account_id,authorization_id,binding_json,download_limit,store_limit,"
                "model_limit,outbound_limit) "
                "VALUES(?,?,?,?,?,?,?)",
                (binding.account_id, identity, scope, downloads, stores, models, outbound),
            )


def remaining_locked(
    db: sqlite3.Connection, binding: Binding, identity: str | None, kind: Kind
) -> int:
    if identity is None:
        return 0
    row = db.execute(
        "SELECT * FROM fiber_feishu_authorizations WHERE account_id=? AND authorization_id=?",
        (binding.account_id, identity),
    ).fetchone()
    if row is None or row["binding_json"] != canonical(asdict(binding)):
        raise InvalidInput("Feishu authorization unavailable or binding changed")
    consumed = db.execute(
        "SELECT count(*) FROM fiber_feishu_consumption "
        "WHERE account_id=? AND authorization_id=? AND kind=?",
        (binding.account_id, identity, kind),
    ).fetchone()[0]
    return max(0, int(row[kind + "_limit"]) - int(consumed))


def reserve_locked(
    db: sqlite3.Connection,
    binding: Binding,
    identity: str | None,
    kind: Kind,
    target: str,
    *,
    unlimited: bool = False,
) -> bool:
    """Inside the operation's transaction, before I/O. Unknown/failed results stay consumed."""
    if db.execute(
        "SELECT 1 FROM fiber_feishu_consumption "
        "WHERE account_id=? AND authorization_id=? AND kind=? AND target=?",
        (binding.account_id, identity, kind, target),
    ).fetchone():
        return False
    remaining = remaining_locked(db, binding, identity, kind)
    if remaining == 0 and not (
        unlimited and kind in {"download", "store", "outbound"} and identity is not None
    ):
        return False
    db.execute(
        "INSERT INTO fiber_feishu_consumption VALUES(?,?,?,?)",
        (binding.account_id, identity, kind, target),
    )
    return True


def remaining(config: Config, binding: Binding, identity: str | None) -> dict[str, int]:
    with weixin_inbox.connection(config, binding) as db:
        return {
            kind: remaining_locked(db, binding, identity, kind)
            for kind in ("download", "store", "model", "outbound")
        }
