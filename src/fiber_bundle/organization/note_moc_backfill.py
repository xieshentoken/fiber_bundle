"""Explicit, scope-checked metadata backfill from existing confirmed MOC memberships."""

from typing import Any

from .. import note_placement
from ..config import IntegrityError
from ..inputs import canonical
from ..objects import digest
from . import catalog, note_moc_fields, note_moc_sync, packages, store


def _relations(config: Any, vault: str) -> str:
    from ..moc_registration import bindings

    return digest(
        canonical(
            {
                "paths": bindings(config, vault),
                "members": {
                    mid: record.members
                    for mid, record in note_placement.records(config, vault).items()
                    if record
                },
            }
        ).encode()
    )


def plan(config: Any, db: Any, client: str, vault: str, identities: set[str] | None = None) -> Any:
    root, allowed = packages.context(config, db, client, vault, write=True)
    entities, _, _ = catalog.snapshot(config, db, client, vault)
    linked = {
        n
        for record in note_placement.records(config, vault).values()
        if record
        for n in record.members
    }
    projections = {r["entity"]: r for r in store.rows(config, "note_moc", vault)}
    items, unchanged, experts = [], 0, 0
    for entity in entities:
        if identities is not None and entity["id"] not in identities:
            continue
        if entity["kind"] != "note":
            experts += 1
            continue
        if entity["id"] not in linked and entity["id"] not in projections:
            continue
        raw = packages.scoped_read(root, allowed, entity["path"])
        wanted, aliases = note_moc_sync.desired(config, vault, entity["id"])

        def resolve(value: str, mapping: dict[str, str] = aliases) -> str:
            target = note_moc_fields.target(value)
            return mapping.get(target, target)

        after, _ = note_moc_fields.render(
            raw, wanted, projections.get(entity["id"], {}).get("owned", []), resolve
        )
        if after == raw:
            unchanged += 1
        else:
            items.append(
                {
                    "entity": entity["id"],
                    "path": entity["path"],
                    "before": digest(raw),
                    "after": digest(after),
                }
            )
    return {
        "vault": vault,
        "client": client,
        "relations": _relations(config, vault),
        "items": items,
        "unchanged": unchanged,
        "experts_untouched": experts,
    }


def apply(config: Any, db: Any, packet: Any) -> Any:
    client, vault = packet["client"], packet["vault"]
    packages.context(config, db, client, vault, write=True)
    note_moc_sync.recover_all(config, db, client, vault)
    if _relations(config, vault) != packet["relations"]:
        raise IntegrityError("note_moc_backfill_relations_changed")
    entities, _, _ = catalog.snapshot(config, db, client, vault)
    by_id = {e["id"]: e for e in entities if e["kind"] == "note"}
    for item in packet["items"]:
        e = by_id.get(item["entity"])
        if (
            e is None
            or e["path"] != item["path"]
            or e["sha256"] not in {item["before"], item["after"]}
        ):
            raise IntegrityError("note_moc_backfill_note_changed")
    written = 0
    for item in packet["items"]:
        entity = by_id[item["entity"]]
        note_moc_sync.track(config, db, client, vault, entity)
        written += note_moc_sync.sync(config, db, client, vault, entity)
    return {"written": written, "experts_untouched": packet["experts_untouched"]}


def sync_members(
    config: Any,
    db: Any,
    client: str,
    vault: str,
    identities: set[str],
) -> bool:
    note_moc_sync.recover_all(config, db, client, vault)
    packet = plan(config, db, client, vault, identities)
    return bool(apply(config, db, packet)["written"])
