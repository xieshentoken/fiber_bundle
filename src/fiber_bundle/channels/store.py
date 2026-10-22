"""Private configuration and idempotent delivery ledger, separate from Vault business data."""

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from .. import auth, vaults
from ..config import Config, InvalidInput, contained, make_directory
from ..inputs import canonical


def connection(config: Config) -> sqlite3.Connection:
    path = contained(config.state_root, "message-channels.sqlite3")
    db = sqlite3.connect(path, isolation_level=None, timeout=5)
    path.chmod(0o600)
    db.row_factory = sqlite3.Row
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version not in {0, 1, 2, 3}:
        db.close()
        raise InvalidInput("channel_schema_unsupported")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA synchronous=EXTRA")
    db.execute("PRAGMA fullfsync=ON")
    db.execute("BEGIN IMMEDIATE")
    try:
        _schema(db)
        db.execute("PRAGMA user_version=3")
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        db.close()
        raise
    return db


def _schema(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS channels (
        id TEXT PRIMARY KEY, revision INTEGER NOT NULL, value TEXT NOT NULL,
        state TEXT NOT NULL, owner TEXT, candidate TEXT, challenge TEXT) STRICT""")
    db.execute("""CREATE TABLE IF NOT EXISTS events (
        channel_id TEXT NOT NULL REFERENCES channels(id), event_id TEXT NOT NULL,
        revision INTEGER NOT NULL, state TEXT NOT NULL, value TEXT NOT NULL,
        created REAL NOT NULL, PRIMARY KEY(channel_id,event_id)) STRICT""")
    db.execute("""CREATE TABLE IF NOT EXISTS legacy_channels (
        id TEXT PRIMARY KEY, revision INTEGER NOT NULL, value TEXT NOT NULL,
        state TEXT NOT NULL) STRICT""")
    db.execute("""CREATE TABLE IF NOT EXISTS channel_operations (
        id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, action TEXT NOT NULL,
        state TEXT NOT NULL, value TEXT NOT NULL, created REAL NOT NULL,
        updated REAL NOT NULL, reason TEXT) STRICT""")
    db.execute(
        "CREATE INDEX IF NOT EXISTS channel_operation_history "
        "ON channel_operations(channel_id,created DESC)"
    )
    from .intake_repository import schema

    schema(db)


@contextmanager
def opened(config: Config) -> Iterator[sqlite3.Connection]:
    db = connection(config)
    try:
        yield db
    finally:
        db.close()


@contextmanager
def readonly(config: Config) -> Iterator[sqlite3.Connection | None]:
    """Status observation must neither create a sidecar nor migrate an existing one."""
    path = contained(config.state_root, "message-channels.sqlite3")
    if not path.exists():
        yield None
        return
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    try:
        if db.execute("PRAGMA user_version").fetchone()[0] not in {1, 2, 3}:
            raise InvalidInput("channel_schema_unsupported")
        yield db
    finally:
        db.close()


def get(db: sqlite3.Connection, identity: str) -> dict[str, Any]:
    try:
        if str(UUID(identity)) != identity:
            raise ValueError()
    except ValueError:
        raise InvalidInput("channel_identifier_invalid") from None
    row = db.execute("SELECT * FROM channels WHERE id=?", (identity,)).fetchone()
    if row is None:
        raise auth.Denied(404)
    return {
        **dict(row),
        "value": json.loads(row["value"]),
        "candidate": json.loads(row["candidate"]) if row["candidate"] else None,
    }


def home(config: Config, identity: str) -> Path:
    # A generated identity, never a renderer-supplied directory or an existing Hermes home.
    UUID(identity)
    return contained(config.state_root, "configs/hermes-feishu-gui-" + identity)


def authorize(config: Config, main: sqlite3.Connection, channel: dict[str, Any]) -> None:
    from .validation import require_permissions

    require_permissions(config, main, channel)


def save(
    config: Config,
    main: sqlite3.Connection,
    value: dict[str, Any],
    identity: str | None,
    expected: int | None,
) -> dict[str, Any]:
    with opened(config) as db:
        old = get(db, identity) if identity else None
        if old:
            from .operations import pending

            if pending(db, old["id"]):
                raise InvalidInput("channel_operation_pending")
        if old and (
            old["revision"] != expected or old["state"] not in {"paused", "unpaired", "error"}
        ):
            raise InvalidInput("channel_pause_or_revision_required")
        if old:
            from .services import control as service_control
            from .services import policy as service_policy

            for role in service_policy.ROLES:
                intent = service_policy.get(config, old["id"], role)
                if intent and (
                    intent["enabled"]
                    or service_control.status(config, old["id"], role).get("process_running")
                ):
                    raise InvalidInput("channel_pause_processing_required")
        if old and old["value"]["platform"] != value["platform"]:
            raise InvalidInput("channel_platform_immutable")
        if value["answer_mode"] == "model":
            from ..model_configuration.policy import resolve

            resolve(config)
        elif value["answer_mode"] != "evidence":
            from ..query_policy_store import checked

            route = checked(config, value["configuration_sha256"])
            if route.local_query is None or (
                value["answer_mode"] == "local_cloud" and route.cloud_answer is None
            ):
                raise InvalidInput("channel_model_policy_unavailable")
        vid, cid = value["vault_id"], value["client_id"]
        vault = main.execute("SELECT * FROM vaults WHERE id=?", (vid,)).fetchone()
        if vault is None:
            raise auth.Denied(404)
        vaults.checked_vault(config, vault)
        from . import query_scope
        from .validation import ChannelValidationError

        client = main.execute("SELECT active FROM clients WHERE id=?", (cid,)).fetchone()
        if client is None or not client["active"]:
            raise ChannelValidationError(["client_unavailable"], status=403)
        if value["add"] and vault["read_only"]:
            raise ChannelValidationError(["add_read_only"], status=403)
        other = [
            json.loads(row[0])
            for row in db.execute(
                "SELECT value FROM channels WHERE id!=? AND state!='removed' "
                "AND json_extract(value,'$.client_id')=? AND json_extract(value,'$.vault_id')=?",
                (identity or "", cid, vid),
            )
        ]
        for item in query_scope.entries(value):
            selected = main.execute(
                "SELECT * FROM vaults WHERE id=?", (item["vault_id"],)
            ).fetchone()
            if selected is None:
                raise auth.Denied(404)
            vaults.checked_vault(config, selected)
        if "query_vaults" not in value:
            auth.set_grant(
                main,
                cid,
                vid,
                query=True,
                original=value["original"] or any(v.get("original") for v in other),
                add=value["add"] or any(v.get("add") for v in other),
            )
        else:
            for item in query_scope.entries(value):
                prior_grant = main.execute(
                    "SELECT * FROM client_vault_grants WHERE client_id=? AND vault_id=?",
                    (cid, item["vault_id"]),
                ).fetchone()
                auth.set_grant(
                    main,
                    cid,
                    item["vault_id"],
                    query=True,
                    original=bool(prior_grant and prior_grant["can_original"]),
                    add=bool(prior_grant and prior_grant["can_add"]),
                )
        identity = identity or str(uuid4())
        folder = home(config, identity)
        make_directory(folder)
        owner = old["owner"] if old else None
        if old and old["value"].get("app_id") != value.get("app_id"):
            owner = None
        revision = old["revision"] + 1 if old else 1
        db.execute(
            "INSERT INTO channels VALUES(?,?,?, ?,?,NULL,NULL) ON CONFLICT(id) DO UPDATE SET "
            "revision=excluded.revision,value=excluded.value,state=excluded.state,"
            "owner=excluded.owner,candidate=NULL,challenge=NULL",
            (identity, revision, canonical(value), "paused" if owner else "unpaired", owner),
        )
        return get(db, identity)


def set_state(db: sqlite3.Connection, identity: str, state: str) -> None:
    db.execute("UPDATE channels SET state=? WHERE id=?", (state, identity))


def invalidate_vault(config: Config, vid: str) -> None:
    if not contained(config.state_root, "message-channels.sqlite3").exists():
        return
    from . import query_scope

    with opened(config) as db:
        identities = [
            r["id"]
            for r in db.execute("SELECT id,value FROM channels WHERE state!='removed'")
            if vid in query_scope.referenced(json.loads(r["value"]))
        ]
        for identity in identities:
            channel = get(db, identity)
            if "query_vaults" not in channel["value"]:
                db.execute(
                    "UPDATE channels SET state='paused',revision=revision+1 WHERE id=?", (identity,)
                )
            for event in db.execute(
                "SELECT event_id,value FROM events WHERE channel_id=? "
                "AND state IN ('captured','processing','gui_processing','ready')",
                (identity,),
            ).fetchall():
                scope = json.loads(event["value"]).get(query_scope.KEY)
                if scope is None or any(v["vault_id"] == vid for v in scope["vaults"]):
                    db.execute(
                        "UPDATE events SET state='cancelled' WHERE channel_id=? AND event_id=?",
                        (identity, event["event_id"]),
                    )


def capture(
    db: sqlite3.Connection,
    channel: dict[str, Any],
    event_id: str,
    owner: str,
    question: str,
    context: str = "",
    *,
    scope: dict[str, Any] | None = None,
    response: dict[str, Any] | None = None,
) -> bool:
    if channel["state"] != "running" or channel["owner"] != owner:
        return False
    count = db.execute(
        "SELECT count(*) FROM events WHERE channel_id=? AND revision=?",
        (channel["id"], channel["revision"]),
    ).fetchone()[0]
    if channel["value"].get("policy_version", 2) != 2 and count >= channel["value"]["query_limit"]:
        return False
    from . import query_scope

    payload = {
        "question": question,
        "context": context,
        query_scope.KEY: scope or query_scope.snapshot(channel),
    }
    if response is not None:
        payload["result"] = response
    return bool(
        db.execute(
            "INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?)",
            (
                channel["id"],
                event_id,
                channel["revision"],
                "ready" if response is not None else "captured",
                canonical(payload),
                time.time(),
            ),
        ).rowcount
    )
