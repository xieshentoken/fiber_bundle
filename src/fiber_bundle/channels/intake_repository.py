"""Policies and archival consumption in the existing private channel sidecar."""

import sqlite3
import time
from collections.abc import Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import asdict
from typing import Any

from .. import auth, database, vaults
from ..config import Config, InvalidInput, contained
from ..inputs import canonical
from ..objects import digest
from ..weixin_events import Binding
from . import intake_contract, legacy, operations, store


def lease(config: Config, already_leased: bool) -> AbstractContextManager[None]:
    """Only authenticated HTTP routes pass True: Guard owns their complete request lease."""
    return nullcontext() if already_leased else database.locked(config)


def schema(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS channel_intake_policies (
        channel_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, value TEXT NOT NULL,
        binding_sha TEXT NOT NULL, after_row INTEGER NOT NULL, updated REAL NOT NULL) STRICT""")
    db.execute("""CREATE TABLE IF NOT EXISTS channel_intake_records (
        channel_id TEXT NOT NULL, batch_id TEXT NOT NULL, revision INTEGER NOT NULL,
        state TEXT NOT NULL, reason TEXT, value TEXT NOT NULL,
        created REAL NOT NULL, updated REAL NOT NULL,
        PRIMARY KEY(channel_id,batch_id)) STRICT""")
    db.execute("""CREATE TABLE IF NOT EXISTS channel_intake_consumption (
        channel_id TEXT NOT NULL, kind TEXT NOT NULL, target TEXT NOT NULL,
        created REAL NOT NULL, PRIMARY KEY(channel_id,kind,target)) STRICT""")


def binding(channel: Mapping[str, Any]) -> Binding:
    value = channel["value"]
    if value.get("platform", "feishu") != "feishu":
        raise InvalidInput("intake_platform_not_supported")
    return Binding(
        "feishu:" + value["app_id"], channel["owner"], value["client_id"], value["vault_id"]
    )


def fingerprint(channel: Mapping[str, Any]) -> str:
    return digest(canonical(asdict(binding(channel))).encode())


def existing(db: sqlite3.Connection | None) -> bool:
    return bool(
        db is not None
        and db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='channel_intake_policies'"
        ).fetchone()
    )


def policy(db: sqlite3.Connection | None, identity: str) -> dict[str, Any]:
    row = (
        db.execute(
            "SELECT * FROM channel_intake_policies WHERE channel_id=?", (identity,)
        ).fetchone()
        if db is not None and existing(db)
        else None
    )
    if row is None:
        return {
            "channel_id": identity,
            "revision": 0,
            "settings": intake_contract.Settings().model_dump(),
            "after_row": 0,
            "binding_sha": None,
            "updated": None,
        }
    return {
        **dict(row),
        "settings": intake_contract.Settings.model_validate_json(row["value"]).model_dump(),
    }


def consumed(db: sqlite3.Connection | None, identity: str) -> dict[str, int]:
    result = {"store": 0, "download": 0, "outbound": 0}
    if db is not None and existing(db):
        result.update(
            {
                row[0]: row[1]
                for row in db.execute(
                    "SELECT kind,count(*) FROM channel_intake_consumption "
                    "WHERE channel_id=? GROUP BY kind",
                    (identity,),
                )
            }
        )
    return result


def present(db: sqlite3.Connection | None, channel: Mapping[str, Any]) -> dict[str, Any]:
    value = policy(db, channel["id"])
    used = consumed(db, channel["id"])
    return {
        "revision": value["revision"],
        "settings": value["settings"],
        "target_vault_id": value["settings"]["target_vault_id"],
        "source_vault_id": channel["value"]["vault_id"],
        "supported": channel["value"].get("platform", "feishu") == "feishu",
        "used": used,
        "remaining": {
            k: None
            if unlimited(value["settings"], k)
            else max(0, value["settings"][k + "_limit"] - used[k])
            for k in used
        },
        "binding_current": not value["revision"] or value["binding_sha"] == fingerprint(channel),
        "updated_at": value["updated"],
    }


def authorize(
    config: Config,
    main: sqlite3.Connection,
    channel: Mapping[str, Any],
    settings: Mapping[str, Any] | None = None,
) -> None:
    if not channel.get("owner") or channel["state"] in {"removed", "unbinding", "removing"}:
        raise InvalidInput("intake_owner_or_channel_unavailable")
    if channel.get("origin") != "legacy" and not channel["value"].get("add"):
        raise InvalidInput("intake_add_permission_required")
    bound = binding(channel)
    target = settings["target_vault_id"] if settings else bound.vault_id
    if not target:
        raise InvalidInput("intake_target_required")
    for identity in {bound.vault_id, target}:
        granted = main.execute(
            "SELECT v.read_only FROM vaults v JOIN client_vault_grants g ON g.vault_id=v.id "
            "JOIN clients c ON c.id=g.client_id WHERE c.id=? AND c.active=1 "
            "AND v.id=? AND g.can_add=1 AND v.status='active' AND v.attachment_state='attached'",
            (bound.client_id, identity),
        ).fetchone()
        if granted is not None and granted["read_only"]:
            raise InvalidInput("intake_read_only_vault")
        vault = auth.grant(main, bound.client_id, identity, "add")
        vaults.checked_vault(config, vault)
        if vault["read_only"]:
            raise InvalidInput("intake_read_only_vault")


def save(
    config: Config, identity: str, data: intake_contract.Save, *, leased: bool = False
) -> dict[str, Any]:
    with lease(config, leased), store.opened(config) as db:
        channel = operations.entry(config, db, identity)
        if channel["state"] in {"removed", "removing", "unbinding"} or operations.pending(
            db, identity
        ):
            raise InvalidInput("channel_operation_pending")
        if channel["value"].get("platform", "feishu") != "feishu":
            raise InvalidInput("intake_platform_not_supported")
        with database.transaction(db):
            prior = policy(db, identity)
            if prior["revision"] != data.expected_revision:
                raise InvalidInput("intake_revision_conflict")
            value = data.settings.model_dump()
            if value["enabled"] and (
                not value["materials"]
                or (not unlimited(value, "store") and value["store_limit"] == 0)
            ):
                raise InvalidInput("intake_store_limit_required")
            if value["enabled"]:
                main = database.connect(config.state_root)
                try:
                    authorize(config, main, channel, value)
                finally:
                    main.close()
            if value == prior["settings"] and prior["binding_sha"] == fingerprint(channel):
                return present(db, channel)
            journal = contained(config.state_root, "weixin-inbox.sqlite3")
            watermark = 0
            if journal.exists():
                with sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True) as inbox:
                    watermark = inbox.execute(
                        "SELECT coalesce(max(rowid),0) FROM events WHERE account_id=?",
                        (binding(channel).account_id,),
                    ).fetchone()[0]
            db.execute(
                "INSERT INTO channel_intake_policies VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(channel_id) DO UPDATE SET revision=excluded.revision,"
                "value=excluded.value,binding_sha=excluded.binding_sha,"
                "after_row=excluded.after_row,updated=excluded.updated",
                (
                    identity,
                    prior["revision"] + 1,
                    canonical(value),
                    fingerprint(channel),
                    watermark,
                    time.time(),
                ),
            )
            db.execute(
                "UPDATE channel_intake_records SET state='manual',reason='intake_policy_changed',"
                "updated=? WHERE channel_id=? AND state IN ('waiting','queued')",
                (time.time(), identity),
            )
            return present(db, channel)


def unlimited(settings: Mapping[str, Any], kind: str) -> bool:
    """Version two records use without business quotas; identities still prevent replays."""
    return settings.get("policy_version", 1) == 2 and kind in {"store", "download", "outbound"}


def reserve(
    db: sqlite3.Connection, identity: str, settings: Mapping[str, Any], kind: str, target: str
) -> bool:
    if db.execute(
        "SELECT 1 FROM channel_intake_consumption WHERE channel_id=? AND kind=? AND target=?",
        (identity, kind, target),
    ).fetchone():
        return False
    used = consumed(db, identity)[kind]
    if not unlimited(settings, kind) and used >= settings[kind + "_limit"]:
        return False
    db.execute(
        "INSERT INTO channel_intake_consumption VALUES(?,?,?,?)",
        (identity, kind, target, time.time()),
    )
    return True


def legacy_id(config: Config, home: str) -> str | None:
    # Stable identity uses the same path rule as the management overview, no directory scan.
    from uuid import NAMESPACE_URL, uuid5

    path = contained(config.state_root, home + "/receiver.json")
    if not path.is_file():
        return None
    return str(uuid5(NAMESPACE_URL, "fiber-legacy-channel:" + home + "/receiver.json"))


def channel(config: Config, identity: str) -> tuple[dict[str, Any], dict[str, Any]]:
    with store.readonly(config) as db:
        if db is None:
            values, _ = legacy.entries(config)
            entry = next((v for v in values if v["id"] == identity), None)
            if entry is None:
                raise auth.Denied(404)
        else:
            entry = operations.entry(config, db, identity)
        return entry, policy(db, identity)
