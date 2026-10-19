"""Current authorized reads and independent human decisions; stale support is never evidence."""

import json
import sqlite3
import time
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from typing import Any

from .. import auth, database, note_inventory, note_provenance, note_search_inventory, vaults
from ..config import Config, IntegrityError, InvalidInput
from . import intake, jobs, policy, store
from .contracts import MAX_OUTPUT_BYTES, bounded_text, fingerprint
from .results import Result, decode_result


@dataclass(frozen=True)
class Generation:
    job_id: str
    profile: policy.Profile
    prepared: intake.Prepared
    result: Result
    visible: frozenset[str]
    paths: dict[str, str]


def generation(
    config: Config,
    cid: str,
    vid: str,
    *,
    job_id: str | None = None,
    inventory_snapshot: note_inventory.Inventory | None = None,
) -> Generation | None:
    # Caller holds storage lock. Authorization precedes graph/vector loading or statistics.
    jobs.authorize(config, cid, vid)
    profile = policy.current(config)
    if profile is None or not profile.enabled:
        return None
    with store.connection(config) as side:
        if side is None:
            return None
        row = (
            side.execute(
                "SELECT * FROM jobs WHERE id=? AND client_id=? AND vault_id=? "
                "AND state='completed'",
                (job_id, cid, vid),
            ).fetchone()
            if job_id
            else side.execute(
                "SELECT j.* FROM generations g JOIN jobs j ON j.id=g.job_id WHERE "
                "g.client_id=? AND g.vault_id=? AND g.state=?",
                (cid, vid, "ready"),
            ).fetchone()
        )
        if row is None:
            return None
        frozen_profile = policy.parse(json.loads(row["profile_json"]))
        prepared = store.decode(row["prepared_json"])
        if profile != frozen_profile:
            return None
        db = database.connect(config.state_root)
        try:
            if intake.epoch(db, cid, vid) != prepared.permission_epoch:
                return None
            vault = auth.grant(db, cid, vid, "query")
            root = vaults.checked_vault(config, vault).stat()
            inventory = inventory_snapshot or note_search_inventory.read(
                config, db, vault, selected={s.note_id for s in prepared.request.notes}
            )
            current = {n.identity: n for n in inventory.notes}
            from ..organization import note_moc_graph
            from ..organization import store as organization_store

            projections = {r["entity"]: r for r in organization_store.rows(config, "note_moc", vid)}
            bindings = {b.note_id: b for b in prepared.bindings}
            visible, paths = set(), {}
            for source in prepared.request.notes:
                note, binding = current.get(source.note_id), bindings[source.note_id]
                note = note_moc_graph.project(
                    note, source, binding, projections.get(source.note_id)
                )
                if (
                    note is None
                    or note.sha256 != source.sha256
                    or note.file_key != binding.file_key
                    or (root.st_dev, root.st_ino) != binding.vault_key
                ):
                    continue
                provenance = note_provenance.describe(config, db, cid, vault, inventory, note)
                if (
                    note.title,
                    note.text,
                    provenance["source_kind"],
                    provenance["source_version_id"],
                ) != (source.title, source.text, source.source_kind, source.source_version_id):
                    continue
                visible.add(source.note_id)
                paths[source.note_id] = note.relative_path
        finally:
            db.close()
        raw = row["result_json"].encode()
        if len(raw) > MAX_OUTPUT_BYTES:
            raise IntegrityError("graph_result_byte_limit")
        return Generation(
            row["id"],
            profile,
            prepared,
            decode_result(raw, prepared.request),
            frozenset(visible),
            paths,
        )


def objects(value: Generation) -> list[dict[str, Any]]:
    candidates = []
    for entity in value.result.entities:
        candidates.append(
            {
                "id": entity.id,
                "type": "entity",
                "value": asdict(entity),
                "support": {entity.evidence.note_id},
            }
        )
    for relation in value.result.relations:
        candidates.append(
            {
                "id": relation.id,
                "type": "relation",
                "value": asdict(relation),
                "support": {s.note_id for s in relation.evidence},
            }
        )
    for moc in value.result.mocs:
        candidates.append(
            {
                "id": fingerprint(asdict(moc)),
                "type": "moc",
                "value": asdict(moc),
                "support": set(moc.note_ids),
            }
        )
    return candidates


def decision(
    db: sqlite3.Connection, cid: str, vid: str, configuration: str, oid: str
) -> dict[str, Any]:
    row = db.execute(
        "SELECT revision,state,reason,reviewed_at FROM reviews WHERE client_id=? AND "
        "vault_id=? AND configuration=? AND object_id=? ORDER BY revision DESC LIMIT 1",
        (cid, vid, configuration, oid),
    ).fetchone()
    return (
        dict(row)
        if row
        else {"revision": 0, "state": "suggested", "reason": "", "reviewed_at": None}
    )


def browse(config: Config, cid: str, vid: str, *, _locked: bool = False) -> dict[str, Any]:
    with nullcontext() if _locked else database.locked(config):
        value = generation(config, cid, vid)
        if value is None:
            return {"state": "unavailable", "job_id": None, "items": []}
        with store.connection(config) as side:
            assert side is not None
            items = []
            for item in objects(value):
                review = decision(side, cid, vid, value.profile.digest, item["id"])
                valid = item["support"] <= value.visible
                items.append(
                    {
                        "id": item["id"],
                        "type": item["type"],
                        "value": item["value"] if valid else None,
                        "review": review
                        if valid
                        else {
                            "revision": review["revision"],
                            "state": "stale",
                            "reason": "来源已变化或当前不可用",
                        },
                        "paths": {key: value.paths[key] for key in sorted(item["support"])}
                        if valid
                        else {},
                    }
                )
        return {
            "state": "ready",
            "job_id": value.job_id,
            "configuration": value.profile.digest,
            "method": value.prepared.request.engine.algorithm,
            "items": items,
        }


def review(
    config: Config,
    cid: str,
    vid: str,
    oid: str,
    *,
    configuration: str,
    revision: int,
    state: str,
    reason: str,
    _locked: bool = False,
    job_id: str | None = None,
) -> dict[str, Any]:
    if (
        state not in {"accepted", "rejected", "suggested"}
        or type(revision) is not int
        or revision < 0
    ):
        raise InvalidInput("graph_invalid_review")
    bounded_text(reason, 400)
    with nullcontext() if _locked else database.locked(config):
        value = generation(config, cid, vid, job_id=job_id)
        if value is None:
            raise auth.Denied(404)
        if value.profile.digest != configuration:
            raise IntegrityError("graph_generation_changed")
        selected = next((item for item in objects(value) if item["id"] == oid), None)
        if selected is None or not selected["support"] <= value.visible:
            raise IntegrityError("graph_suggestion_stale")
        with store.connection(config, write=True) as side:
            assert side is not None
            with database.transaction(side):
                current = decision(side, cid, vid, configuration, oid)
                if current["revision"] != revision:
                    raise IntegrityError("graph_review_revision_conflict")
                side.execute(
                    "INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?)",
                    (cid, vid, configuration, oid, revision + 1, state, reason, time.time()),
                )
                return decision(side, cid, vid, configuration, oid)
