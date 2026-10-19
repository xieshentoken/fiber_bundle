"""Restore only merge-owned bindings and frozen entrance configuration, preserving audit history."""

import json
import os
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from . import database, moc_store, note_identity_store, publication_locations
from .config import Config, IntegrityError
from .inputs import canonical


def validate(config: Config, db: Any, identity: str, value: dict[str, Any]) -> None:
    from .vault_merge_switch import check_committed
    from .vault_merge_switch import entrances as current_entrances

    selection = value["plan"]["selection"]
    expected_main = value.get("rollback_main_snapshot")
    if expected_main and main_snapshot(db, selection) != expected_main:
        raise IntegrityError("vault_merge_rollback_permission_changed")
    if value.get("switched") and not value.get("rollback_started"):
        check_committed(config, db, value)
    elif not value.get("switched") and not value.get("rollback_started"):
        from .vault_merge_inventory import context

        for source in value["plan"]["inventories"]:
            if (
                context(config, db, selection["client_id"], source["context"]["vault_id"])
                != source["context"]
            ):
                raise IntegrityError("vault_merge_rollback_scope_changed")
    now = current_entrances(
        config,
        db,
        selection["source_vault_ids"],
        selection["target_vault_id"],
        selection["switch_channel_ids"],
    )
    expected = (
        value.get("rollback_sidecar_expected")
        or value.get("sidecar_expected")
        or value.get("paused_entrances")
        or value["plan"]["entrances"]
    )
    previous = value.get("rollback_sidecar_before") or value.get("paused_entrances") or expected
    side_keys = ("channels", "intake")

    def side_matches(snapshot: dict[str, Any]) -> bool:
        # During pause, a channel can already be stopped before the parent stage journal.
        import copy

        snapshot = copy.deepcopy(snapshot)
        if value["phase"] == "pausing":
            for channel in snapshot["channels"]:
                actual = next((c for c in now["channels"] if c["id"] == channel["id"]), None)
                if actual and actual["state"] in {"paused", "unpaired"}:
                    channel["state"] = actual["state"]
        return all(now[k] == snapshot[k] for k in side_keys)

    if not side_matches(expected) and not side_matches(previous):
        raise IntegrityError("vault_merge_rollback_entrance_changed")
    target = value["plan"]["selection"]["target_vault_id"]
    expected_notes = {f["note"]["identity"]: f for f in value["plan"]["files"] if f["note"]}
    for record in note_identity_store.records(config, target):
        if record.identity not in expected_notes:
            continue
        file = expected_notes[record.identity]
        progress = value["progress"].get(str(value["plan"]["files"].index(file)))
        if (
            not progress
            or record.relative_path != file["target_path"]
            or record.sha256 != file["projected_sha256"]
            or list(record.file_key) != progress["key"]
        ):
            raise IntegrityError("vault_merge_identity_changed_before_rollback")
    for source in value["plan"]["inventories"][:-1]:
        for pub in source["publications"]:
            current = publication_locations.get(db, pub["kind"], pub["id"])
            if current and current["plan"].get("merge_operation_id") == identity:
                continue
            if current is not None and (
                pub["location"] is None or current["value"] != pub["location"]["value"]
            ):
                raise IntegrityError("vault_merge_publication_changed_before_rollback")
        for moc in source["mocs"]:
            current_moc = moc_store.load(config, target, moc["id"])
            if current_moc and (
                current_moc.members != moc["record"]["members"]
                or current_moc.title != moc["record"]["title"]
                or current_moc.revision != value.get("moc_revisions", {}).get(moc["id"], 1)
            ):
                raise IntegrityError("vault_merge_moc_changed_before_rollback")


def bindings(config: Config, db: Any, identity: str, value: dict[str, Any]) -> None:
    target = value["plan"]["selection"]["target_vault_id"]
    from .vault_merge_journal import save

    with database.transaction(db):
        for source in value["plan"]["inventories"][:-1]:
            for pub in source["publications"]:
                current = publication_locations.get(db, pub["kind"], pub["id"])
                if not current or current["plan"].get("merge_operation_id") != identity:
                    continue
                old = pub["location"]
                if old:
                    db.execute(
                        (
                            "UPDATE publication_locations SET "
                            "vault_id=?,entry_note_path=?,package_root=?,revision=?,"
                            "state=?,value=? "
                            "WHERE kind=? AND publication_id=?"
                        ),
                        (
                            old["vault_id"],
                            old["entry_note_path"],
                            old["package_root"],
                            old["revision"],
                            old["state"],
                            old["value"],
                            pub["kind"],
                            pub["id"],
                        ),
                    )
                else:
                    db.execute(
                        "DELETE FROM publication_locations WHERE kind=? AND publication_id=?",
                        (pub["kind"], pub["id"]),
                    )
    wanted = {f["note"]["identity"] for f in value["plan"]["files"] if f["note"]}
    with note_identity_store.connection(config, write=True) as identity_side:
        assert identity_side is not None
        with database.transaction(identity_side):
            for note in wanted:
                identity_side.execute(
                    "DELETE FROM registrations WHERE vault_id=? AND identity=?", (target, note)
                )
        # Immutable events stay as evidence of a rolled-back registration.
    with moc_store.connection(config, write=True) as moc_side:
        assert moc_side is not None
        with database.transaction(moc_side):
            for source in value["plan"]["inventories"][:-1]:
                for moc in source["mocs"]:
                    moc_side.execute(
                        "DELETE FROM mocs WHERE vault_id=? AND id=?", (target, moc["id"])
                    )
    # Keep copied MOC baseline journals in the private rollback audit, outside the live MOC chain.
    from . import moc_file_journal
    from .config import make_directory, sync_directory

    for source in value["plan"]["inventories"][:-1]:
        for moc in source["mocs"]:
            operation = str(uuid5(NAMESPACE_URL, identity + ":moc:" + moc["id"]))
            for plan in moc_file_journal.plans(config, target, moc["id"]):
                if plan.value["operation"] != operation:
                    continue
                destination = (
                    config.state_root / "vault-merge-rollback" / identity / moc["id"] / plan.plan_id
                )
                make_directory(destination.parent)
                if destination.exists():
                    raise IntegrityError("vault_merge_rollback_audit_exists")
                os.rename(plan.path, destination)
                sync_directory(destination.parent)
                sync_directory(plan.path.parent)
    value["bindings_rolled_back"] = True
    save(db, identity, value, "rolling_back")


def entrances(config: Config, db: Any, identity: str, value: dict[str, Any]) -> None:
    """No channel/Agent restart; grant epochs advance even when restoring old capabilities."""
    import time

    from . import auth
    from .channels import store
    from .objects import digest
    from .vault_merge_journal import save
    from .vault_merge_switch import entrances as current_entrances

    selection = value["plan"]["selection"]
    sources, target = selection["source_vault_ids"], selection["target_vault_id"]
    if value.get("entrances_rolled_back"):
        return
    if value.get("switched") and not value.get("main_rolled_back"):
        with database.transaction(db):
            for inventory in value["plan"]["inventories"]:
                vid = inventory["context"]["vault_id"]
                old = inventory["context"]["row"]
                db.execute(
                    "UPDATE vaults SET read_only=?,default_query=? WHERE id=?",
                    (old["read_only"], old["default_query"], vid),
                )
                before = {r["client_id"]: r for r in inventory["context"]["grants"]}
                clients = {
                    r[0]
                    for r in db.execute(
                        "SELECT client_id FROM client_vault_grants WHERE vault_id=?", (vid,)
                    )
                } | set(before)
                for client in clients:
                    grant = before.get(client, {})
                    auth.set_grant(
                        db,
                        client,
                        vid,
                        query=bool(grant.get("can_query")),
                        original=bool(grant.get("can_original")),
                        add=bool(grant.get("can_add")),
                    )
            if selection["switch_default"]:
                db.execute("UPDATE vaults SET default_query=0 WHERE default_query=1")
                for vid in value["plan"]["entrances"]["default_vaults"]:
                    db.execute("UPDATE vaults SET default_query=1 WHERE id=?", (vid,))
            for old in value["plan"]["entrances"]["agent_connections"]:
                cfg = json.loads(old["configuration_json"])
                if not set(cfg["vault_ids"]).intersection(sources + [target]):
                    continue
                db.execute(
                    (
                        "UPDATE agent_connections SET state='paused',configuration_json=?,"
                        "configuration_sha256=?,revision=revision+1,applied_json=NULL,prob"
                        "e_json=NULL,test_json=NULL,updated_at=? WHERE id=?"
                    ),
                    (
                        old["configuration_json"],
                        digest(old["configuration_json"].encode()),
                        time.time(),
                        old["id"],
                    ),
                )
            value["main_rolled_back"] = True
            value["rollback_main_snapshot"] = main_snapshot(db, selection)
            save(db, identity, value, "rolling_back")
    if value.get("sidecar_expected"):
        now = current_entrances(config, db, sources, target, selection["switch_channel_ids"])
        if "rollback_sidecar_expected" not in value:
            from copy import deepcopy

            expected = deepcopy(now)
            for old in value["paused_entrances"]["channels"]:
                channel = next(c for c in expected["channels"] if c["id"] == old["id"])
                if channel["id"] in selection["switch_channel_ids"]:
                    channel["value"] = old["value"]
                    channel["revision"] += 1
                    channel["state"] = "paused" if channel["owner"] else "unpaired"
            for old in value["paused_entrances"]["intake"]:
                row = next(r for r in expected["intake"] if r["channel_id"] == old["channel_id"])
                if row["channel_id"] in selection["switch_channel_ids"]:
                    settings = json.loads(old["value"])
                    settings["enabled"] = False
                    row.update(
                        value=canonical(settings), revision=row["revision"] + 1, updated=time.time()
                    )
            value["rollback_sidecar_before"] = now
            value["rollback_sidecar_expected"] = expected
            save(db, identity, value, "rolling_back")
        if now == value["rollback_sidecar_before"]:
            expected = value["rollback_sidecar_expected"]
            if expected["channels"]:
                with store.opened(config) as side, database.transaction(side):
                    for channel in expected["channels"]:
                        if channel["id"] in selection["switch_channel_ids"]:
                            side.execute(
                                "UPDATE channels SET value=?,revision=?,state=? WHERE id=?",
                                (
                                    canonical(channel["value"]),
                                    channel["revision"],
                                    channel["state"],
                                    channel["id"],
                                ),
                            )
                    for row in expected["intake"]:
                        if row["channel_id"] in selection["switch_channel_ids"]:
                            side.execute(
                                (
                                    "UPDATE channel_intake_policies SET "
                                    "value=?,revision=?,updated=? WHERE "
                                    "channel_id=?"
                                ),
                                (row["value"], row["revision"], row["updated"], row["channel_id"]),
                            )
        elif now != value["rollback_sidecar_expected"]:
            raise IntegrityError("vault_merge_rollback_entrance_changed")
    value["entrances_rolled_back"] = True
    save(db, identity, value, "rolling_back")


def main_snapshot(db: Any, selection: dict[str, Any]) -> dict[str, Any]:
    from .vault_merge_switch import scoped_grants, scoped_vaults

    ids = selection["source_vault_ids"] + [selection["target_vault_id"]]
    return {
        "vaults": scoped_vaults(db, ids),
        "grants": scoped_grants(db, ids),
        "agents": [dict(r) for r in db.execute("SELECT * FROM agent_connections ORDER BY id")],
    }
