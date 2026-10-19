"""Frozen entrance and permission changes for a unified Vault; no automatic channel restart."""

from typing import Any

from .config import Config, InvalidInput


def entrances(
    config: Config, db: Any, sources: list[str], target: str, selected: list[str]
) -> dict[str, Any]:
    from .channels import store

    with store.readonly(config) as side:
        channels = (
            []
            if side is None
            else [
                store.get(side, r[0])
                for r in side.execute("SELECT id FROM channels WHERE state!='removed' ORDER BY id")
            ]
        )
    relevant = [
        c for c in channels if c["value"]["vault_id"] in [*sources, target] or c["id"] in selected
    ]
    if set(selected) - {c["id"] for c in relevant}:
        raise InvalidInput("vault_merge_channel_unavailable")
    # Include the intake target as a separate entrance, not an inferred default.
    with store.readonly(config) as side:
        intake = (
            []
            if side is None
            else [
                dict(r)
                for r in side.execute("SELECT * FROM channel_intake_policies ORDER BY channel_id")
            ]
        )
    return {
        "channels": relevant,
        "intake": intake,
        "default_vaults": [
            r[0] for r in db.execute("SELECT id FROM vaults WHERE default_query=1 ORDER BY id")
        ],
        "agent_connections": [
            dict(r) for r in db.execute("SELECT * FROM agent_connections ORDER BY id")
        ],
    }


def pause(config: Config, db: Any, value: dict[str, Any]) -> bool:
    from .channels import control, store

    wanted = value["plan"]["entrances"]
    for channel in wanted["channels"]:
        with store.readonly(config) as side:
            if side is None:
                raise InvalidInput("vault_merge_channel_unavailable")
            current = store.get(side, channel["id"])
        if current["revision"] != channel["revision"] or current["value"] != channel["value"]:
            raise InvalidInput("vault_merge_channel_configuration_changed")
        if current["state"] not in {"paused", "unpaired"}:
            control.pause(config, db, channel["id"])
        if control.status(config, channel["id"])["process_state"] != "stopped":
            return False
    selection = value["plan"]["selection"]
    value["paused_entrances"] = entrances(
        config,
        db,
        selection["source_vault_ids"],
        selection["target_vault_id"],
        selection["switch_channel_ids"],
    )
    return True


def apply(config: Config, db: Any, identity: str, value: dict[str, Any]) -> None:
    import json
    import time

    from . import auth, database
    from .channels import store
    from .inputs import canonical
    from .objects import digest

    selection = value["plan"]["selection"]
    sources = selection["source_vault_ids"]
    target = selection["target_vault_id"]
    current = entrances(config, db, sources, target, selection["switch_channel_ids"])
    from copy import deepcopy

    from .vault_merge_journal import save

    if "sidecar_expected" not in value:
        if current != value["paused_entrances"]:
            raise InvalidInput("vault_merge_entrance_changed")
        expected = deepcopy(current)
        moment = time.time()
        for channel in expected["channels"]:
            if channel["id"] in selection["switch_channel_ids"]:
                channel["value"]["vault_id"] = target
                channel["revision"] += 1
                channel["state"] = "paused" if channel["owner"] else "unpaired"
        for row in expected["intake"]:
            if row["channel_id"] in selection["switch_channel_ids"]:
                settings = json.loads(row["value"])
                settings.update(target_vault_id=target, enabled=False)
                row["value"] = canonical(settings)
                row["revision"] += 1
                row["updated"] = moment
        value["sidecar_expected"] = expected
        save(
            db, identity, value, "switching"
        )  # Persist exact expected result before the sidecar commit.
    expected = value["sidecar_expected"]
    if current == value["paused_entrances"]:
        if current["channels"]:
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
    elif current != expected:
        raise InvalidInput("vault_merge_entrance_changed")
    value["sidecar_switched"] = True
    save(db, identity, value, "switching")
    with database.transaction(db):
        for source in sources:
            db.execute(
                (
                    "UPDATE client_vault_grants SET can_query=0,can_original=0,can_add=0 WHERE "
                    "vault_id=?"
                ),
                (source,),
            )
            db.execute("UPDATE vaults SET read_only=1,default_query=0 WHERE id=?", (source,))
            db.execute(
                (
                    "UPDATE automatic_jobs SET "
                    "state='cancelled',reason='vault_merged_no_replay' WHERE vault_id=? AND "
                    "state!='complete'"
                ),
                (source,),
            )
        allowed = {g["client_id"]: g for g in selection["target_grants"]}
        for grant_row in db.execute(
            "SELECT client_id FROM client_vault_grants WHERE vault_id=?", (target,)
        ).fetchall():
            auth.set_grant(db, grant_row[0], target, query=False, original=False, add=False)
        for client, grant in allowed.items():
            auth.set_grant(
                db,
                client,
                target,
                query=grant["query"],
                original=grant["original"],
                add=grant["add"],
            )
        for connection in current["agent_connections"]:
            cfg = json.loads(connection["configuration_json"])
            if not set(cfg["vault_ids"]).intersection(sources + [target]):
                continue
            cfg["vault_ids"] = [v for v in cfg["vault_ids"] if v not in sources + [target]]
            if connection["client_id"] in allowed and allowed[connection["client_id"]]["query"]:
                cfg["vault_ids"] = sorted(set([*cfg["vault_ids"], target]))
            db.execute(
                (
                    "UPDATE agent_connections SET state='paused',configuration_json=?,"
                    "configuration_sha256=?,revision=revision+1,applied_json=NULL,prob"
                    "e_json=NULL,test_json=NULL,updated_at=? WHERE id=?"
                ),
                (canonical(cfg), digest(canonical(cfg).encode()), time.time(), connection["id"]),
            )
        if selection["switch_default"]:
            db.execute("UPDATE vaults SET default_query=0 WHERE default_query=1")
            db.execute("UPDATE vaults SET default_query=1 WHERE id=?", (target,))
        # Existing target automatic jobs are not upgraded to the new audience or epochs.
        db.execute(
            (
                "UPDATE automatic_jobs SET "
                "state='cancelled',reason='vault_merge_permission_epoch_changed' WHERE "
                "vault_id=? AND state!='complete'"
            ),
            (target,),
        )
        value["switched"] = {
            "vault_rows": scoped_vaults(db, sources + [target]),
            "grants": scoped_grants(db, sources + [target]),
            "entrances": entrances(config, db, sources, target, selection["switch_channel_ids"]),
        }
        from .vault_merge_journal import save

        value["phase"] = "index"
        save(db, identity, value, "index")


def check_committed(config: Config, db: Any, value: dict[str, Any]) -> None:
    from . import auth, vault_directories

    selection = value["plan"]["selection"]
    committed = value["switched"]
    for source in value["plan"]["inventories"]:
        root, _, _ = vault_directories.vault_root(config, db, source["context"]["vault_id"])
        if vault_directories.key(root) != source["context"]["root_key"]:
            raise InvalidInput("vault_merge_committed_root_changed")
    auth.grant(db, selection["client_id"], selection["target_vault_id"], "query")
    affected = selection["source_vault_ids"] + [selection["target_vault_id"]]
    if (
        scoped_vaults(db, affected) != committed["vault_rows"]
        or scoped_grants(db, affected) != committed["grants"]
    ):
        raise InvalidInput("vault_merge_committed_permission_changed")
    if (
        entrances(
            config,
            db,
            selection["source_vault_ids"],
            selection["target_vault_id"],
            selection["switch_channel_ids"],
        )
        != committed["entrances"]
    ):
        raise InvalidInput("vault_merge_committed_entrance_changed")


def scoped_vaults(db: Any, ids: list[str]) -> list[dict[str, Any]]:
    return [dict(r) for r in db.execute("SELECT * FROM vaults ORDER BY id") if r["id"] in ids]


def scoped_grants(db: Any, ids: list[str]) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in db.execute("SELECT * FROM client_vault_grants ORDER BY client_id,vault_id")
        if r["vault_id"] in ids
    ]
