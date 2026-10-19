"""CAS MOC ownership transfer. Source human ownership and all audit history survive."""

from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from . import moc_store, note_inventory, note_migration
from . import note_migration_plans as plans
from .config import Config, IntegrityError


def record(raw: Any) -> moc_store.Record:
    if not isinstance(raw, dict) or set(raw) != {
        "title",
        "vault_dev",
        "vault_ino",
        "revision",
        "members",
    }:
        raise IntegrityError("invalid migration MOC record")
    value = moc_store.Record(**raw)
    moc_store.validate(value)
    return value


def freeze(
    config: Config, plan: dict[str, Any], target_moc: str | None, revision: int | None
) -> list[dict[str, Any]]:
    changes = []
    identity = plan["note_identity"]
    with moc_store.connection(config) as db:
        ids = (
            []
            if db is None
            else [
                r[0]
                for r in db.execute(
                    "SELECT id FROM mocs WHERE vault_id=? ORDER BY id LIMIT 1001",
                    (plan["source_vault_id"],),
                )
            ]
        )
    if len(ids) > 1000:
        raise IntegrityError("migration MOC observation limit exceeded")
    for mid in ids:
        before = moc_store.load(config, plan["source_vault_id"], mid)
        assert before is not None
        if "system" in before.members.get(identity, []):
            members = {k: v.copy() for k, v in before.members.items()}
            owners = [o for o in members[identity] if o != "system"]
            if owners:
                members[identity] = owners
            else:
                del members[identity]
            after = replace(before, members=members, revision=before.revision + 1)
            changes.append(
                {
                    "vault_id": plan["source_vault_id"],
                    "moc_id": mid,
                    "before": asdict(before),
                    "after": asdict(after),
                }
            )
    if target_moc is not None:
        from . import note_changes

        note_changes.identifier(target_moc)
        before = moc_store.load(config, plan["target_vault_id"], target_moc)
        if before is None or type(revision) is not int or before.revision != revision:
            raise IntegrityError("migration target MOC revision conflict")
        if [before.vault_dev, before.vault_ino] != plan["target_vault_key"]:
            raise IntegrityError("migration target MOC root changed")
        members = {k: v.copy() for k, v in before.members.items()}
        members[identity] = sorted(set(members.get(identity, [])) | {"system"})
        if members != before.members:
            after = replace(before, members=members, revision=before.revision + 1)
            changes.append(
                {
                    "vault_id": plan["target_vault_id"],
                    "moc_id": target_moc,
                    "before": asdict(before),
                    "after": asdict(after),
                }
            )
    elif revision is not None:
        raise IntegrityError("migration MOC revision requires explicit target")
    return changes


def validate(changes: Any, plan: dict[str, Any]) -> None:
    from . import note_changes

    if not isinstance(changes, list) or len(changes) > 1001:
        raise IntegrityError("invalid migration MOC change scope")
    seen = set()
    for change in changes:
        if not isinstance(change, dict) or set(change) != {"vault_id", "moc_id", "before", "after"}:
            raise IntegrityError("invalid migration MOC change")
        note_changes.identifier(change["moc_id"])
        scope = change["vault_id"], change["moc_id"]
        if scope in seen or change["vault_id"] not in {
            plan["source_vault_id"],
            plan["target_vault_id"],
        }:
            raise IntegrityError("invalid migration MOC scope")
        seen.add(scope)
        before, after = record(change["before"]), record(change["after"])
        field = (
            "source_vault_key"
            if change["vault_id"] == plan["source_vault_id"]
            else "target_vault_key"
        )
        if [before.vault_dev, before.vault_ino] != plan[field]:
            raise IntegrityError("migration MOC root binding changed")
        members = {k: v.copy() for k, v in before.members.items()}
        identity = plan["note_identity"]
        if change["vault_id"] == plan["source_vault_id"]:
            owners = [o for o in members.get(identity, []) if o != "system"]
            if owners:
                members[identity] = owners
            else:
                members.pop(identity, None)
        else:
            if sum(c["vault_id"] == plan["target_vault_id"] for c in changes) > 1:
                raise IntegrityError("migration only permits one explicit target MOC")
            members[identity] = sorted(set(members.get(identity, [])) | {"system"})
        if after != replace(before, members=members, revision=before.revision + 1):
            raise IntegrityError("migration MOC delta would change unrelated ownership")


def check(
    config: Config, plan: dict[str, Any], changes: list[dict[str, Any]], roots: list[Path]
) -> None:
    for root, vid, field in zip(
        roots,
        (plan["source_vault_id"], plan["target_vault_id"]),
        ("source_mocs_sha256", "target_mocs_sha256"),
        strict=True,
    ):
        current = note_migration.associations(
            config, vid, note_inventory.Inventory(root, [], {}, {}, {})
        )
        for change in changes:
            if change["vault_id"] == vid:
                mid = change["moc_id"]
                if current.get(mid) not in (change["before"], change["after"]):
                    raise IntegrityError("migration MOC evidence changed")
                current[mid] = change["before"]
        if plans.digest(current) != plan[field]:
            raise IntegrityError("migration unrelated MOC ownership changed")


def apply(
    config: Config,
    client: str,
    execution: str,
    changes: list[dict[str, Any]],
    *,
    guard: Callable[[], None],
) -> None:
    for change in changes:
        before, after = record(change["before"]), record(change["after"])
        current = moc_store.load(config, change["vault_id"], change["moc_id"])
        if current == after:
            continue
        if current != before:
            raise IntegrityError("migration MOC revision conflict")
        guard()
        moc_store.save(
            config,
            change["vault_id"],
            change["moc_id"],
            client,
            after,
            "note-migrate:" + execution,
            before.revision,
        )
