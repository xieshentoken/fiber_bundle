"""Explicit manual selections, preflighted together and projected through existing journals."""

from typing import Any

from ..config import InvalidInput, StorageError
from . import jobs, maintenance_moc_recovery, membership, note_moc_sync, packages, store


def failure(error: Exception) -> str:
    code = str(error)
    return (
        code
        if code.startswith(("maintenance_moc_", "note_moc_", "organization_"))
        else {
            "MOC managed block missing or human edited": "maintenance_moc_managed_block_edited",
            "MOC managed markers conflict": "maintenance_moc_managed_block_edited",
            "MOC managed marker layout invalid": "maintenance_moc_managed_block_edited",
        }.get(code, "organization_manual_write_failed")
    )


def apply(
    config: Any, db: Any, client: str, vault: str, entity_id: str, moc_ids: list[str], action: str
) -> Any:
    packages.context(config, db, client, vault, write=True)
    note_moc_sync.recover_all(config, db, client, vault)
    entities, mocs, _ = jobs.snapshot(config, db, client, vault)
    entity = next((e for e in entities if e["id"] == entity_id), None)
    selected = list(dict.fromkeys(moc_ids))
    targets = [m for mid in selected for m in mocs if m["id"] == mid]
    if entity is None or len(targets) != len(selected) or not selected:
        raise InvalidInput("organization_member_not_visible")
    # A model analysis failure is not a prerequisite for an explicit human decision.
    packet: dict[str, Any] = {
        "client_id": client,
        "vault_id": vault,
        "mocs": targets,
        "items": [{"add": selected, "remove": []}],
    }
    for moc in targets:
        prior = store.get(config, "decisions", membership.decision_key(vault, entity_id, moc["id"]))
        if prior and prior["state"] == "pinned" and "manual" in moc["members"].get(entity_id, []):
            # A previous explicit choice can finish an exact pending exchange.
            # The file journal still verifies both inodes and candidate hashes.
            packet["items"].append({"add": [], "remove": [], "intent": "link:" + moc["id"]})
    if action != "unlock":
        try:
            note_moc_sync.check(config, db, client, vault, entity)
            maintenance_moc_recovery.preflight(config, db, packet)
        except (StorageError, OSError) as error:
            return {
                "changed": False,
                "completed": False,
                "results": [
                    {
                        "moc_id": m["id"],
                        "title": m["title"],
                        "completed": False,
                        "error": failure(error),
                    }
                    for m in targets
                ],
            }
    results = []
    for moc in targets:
        try:
            changed = membership.apply(
                config,
                db,
                client,
                vault,
                entity,
                moc,
                owner="manual",
                action=action,
                missing_block_repair=maintenance_moc_recovery.repair_base(
                    packet, moc["id"], moc["sha256"]
                ),
            )
            results.append(
                {"moc_id": moc["id"], "title": moc["title"], "completed": True, "changed": changed}
            )
        except (StorageError, OSError) as error:
            # Prior selections may already be committed. Report each outcome;
            # retry is idempotent and finishes journals rather than duplicating links.
            results.append(
                {
                    "moc_id": moc["id"],
                    "title": moc["title"],
                    "completed": False,
                    "error": failure(error),
                }
            )
    result = {
        "changed": any(r.get("changed") for r in results),
        "completed": all(r["completed"] for r in results),
        "results": results,
    }
    if action == "link" and result["completed"]:
        from . import manual_resolution, manual_resolution_proof

        proof = manual_resolution_proof.verify(config, db, client, vault, entity, mocs, selected)
        result["resolved_items"] = manual_resolution.record(config, client, vault, proof)
    if result["completed"] and result["changed"]:
        from uuid import uuid4

        from ..backup_automation.events import completed

        completed(config, "manual:" + vault + ":" + entity_id + ":" + str(uuid4()), results)
    return result
