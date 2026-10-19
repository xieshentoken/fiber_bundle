"""Verify completed projections before issuing or backfilling manual completion receipts."""

from dataclasses import asdict
from typing import Any

from .. import moc_file_journal, moc_markdown, moc_store
from ..config import IntegrityError, StorageError
from ..objects import digest
from . import jobs, manual_resolution, note_moc_fields, note_moc_sync, packages, store


def verify(
    config: Any, db: Any, client: str, vault: str, entity: Any, mocs: Any, selected: list[str]
) -> Any:
    root, allowed = packages.context(config, db, client, vault, write=True)
    visible = {m["id"]: m for m in mocs}
    evidence = {}
    for mid in sorted(set(selected)):
        moc = visible.get(mid)
        current = moc_store.load(config, vault, mid) if moc else None
        if moc is None or current is None or "manual" not in current.members.get(entity["id"], []):
            raise IntegrityError("organization_manual_projection_incomplete")
        plans = moc_file_journal.plans(config, vault, mid)
        head = moc_file_journal.latest(plans)
        raw = packages.scoped_read(root, allowed, moc["path"])
        _, block, _ = moc_markdown.split(raw, mid)
        if (
            not head
            or any(p.status == "pending" for p in plans)
            or head.value["revision"] != current.revision
            or digest(block) != head.value["managed_sha256"]
        ):
            raise IntegrityError("organization_manual_projection_incomplete")
        evidence[mid] = {
            "sha256": digest(raw),
            "revision": current.revision,
            "members": asdict(current)["members"],
        }
    if not evidence:
        raise IntegrityError("organization_manual_projection_incomplete")
    source_sha = entity["sha256"]
    versions = {source_sha}
    if entity["kind"] == "note":
        raw = packages.scoped_read(root, allowed, entity["path"])
        source_sha = digest(raw)
        projection = store.get(config, "note_moc", note_moc_sync.key(vault, entity["id"]))
        if projection and projection.get("pending"):
            raise IntegrityError("organization_manual_projection_incomplete")
        wanted, aliases = note_moc_sync.desired(config, vault, entity["id"])
        present = {
            aliases.get(note_moc_fields.target(v), note_moc_fields.target(v))
            for v in note_moc_fields.field(raw.decode("utf-8"))[2]
        }
        if not {note_moc_fields.target(v) for v in wanted} <= present:
            raise IntegrityError("organization_manual_projection_incomplete")
        # A fresh body edit with already-correct moc values needs no exchange.
        # Its current version may be resolved, but never inherit the stale body's lineage.
        if projection and projection["after_sha256"] == source_sha:
            versions = {v["sha256"] for v in projection["versions"]}
        else:
            versions = {source_sha}
        versions.add(source_sha)
    return {
        "entity": entity["id"],
        "path": entity["path"],
        "source_sha256": source_sha,
        "source_versions": sorted(versions),
        "moc_ids": sorted(evidence),
        "moc_evidence": evidence,
    }


def backfill(config: Any, db: Any, client: str, vault: str) -> Any:
    entities, mocs, _ = jobs.snapshot(config, db, client, vault)
    pinned: dict[str, list[str]] = {}
    for d in store.rows(config, "decisions", vault):
        if d["state"] == "pinned":
            pinned.setdefault(d["entity"], []).append(d["moc"])
    changed = []
    skipped = []
    for entity in entities:
        if entity["id"] not in pinned:
            continue
        try:
            proof = verify(config, db, client, vault, entity, mocs, pinned[entity["id"]])
            count = manual_resolution.record(config, client, vault, proof)
            if count:
                changed.append({"entity": entity["id"], "title": entity["title"], "items": count})
        except (StorageError, OSError) as error:
            skipped.append({"entity": entity["id"], "reason": str(error)[:100]})
    return {"updated": changed, "skipped": skipped}
