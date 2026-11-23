"""Read bounded association evidence once for projection and freshness checks."""

import json
from dataclasses import asdict, dataclass
from typing import Any

from .. import moc_file_journal, note_identities, note_identity_store, note_placement
from ..config import Config, IntegrityError
from ..organization import store
from .contracts import fingerprint


@dataclass(frozen=True)
class Ledger:
    registrations: dict[str, note_identity_store.Registration]
    records: dict[str, Any]
    plans: dict[str, Any]
    pending: frozenset[str]
    pending_plans: dict[str, list[dict[str, Any]]]
    decisions: dict[str, dict[str, Any]]
    titles: dict[str, dict[str, Any]]
    history: dict[str, list[str]]
    token: str


def read(config: Config, vault: str) -> Ledger:
    registrations = {
        record.identity: record for record in note_identity_store.records(config, vault)
    }
    for record in registrations.values():
        note_identities.validate(record)
    records = note_placement.records(config, vault)
    history: dict[str, list[str]] = {}
    with note_identity_store.connection(config) as side:
        if side:
            for number, row in enumerate(
                side.execute(
                    "SELECT identity,json_extract(registration_json,'$.path') "
                    "FROM events WHERE vault_id=? ORDER BY id",
                    (vault,),
                )
            ):
                if number >= 200_000:
                    raise IntegrityError("note cloud identity history limit")
                if isinstance(row[1], str):
                    history.setdefault(row[0], []).append(row[1])
    plans = {}
    pending = set()
    pending_plans = {}
    proof = []
    for mid in records:
        values = moc_file_journal.plans(config, vault, mid)
        current = moc_file_journal.latest(values)
        if current:
            plans[mid] = current.value
        if any(item.status == "pending" for item in values):
            pending.add(mid)
            pending_plans[mid] = [item.value for item in values if item.status == "pending"]
        proof.append([mid, [[item.plan_id, item.status, item.value] for item in values]])
    decisions: dict[str, dict[str, Any]] = {}
    decision_pairs: dict[tuple[Any, Any], dict[str, Any]] = {}
    titles: dict[str, dict[str, Any]] = {}
    with store.connection(config) as side:
        if side:
            names = {
                row[0] for row in side.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "decisions" in names:
                for number, row in enumerate(
                    side.execute(
                        "SELECT id,value FROM decisions WHERE vault=? ORDER BY id", (vault,)
                    )
                ):
                    if number >= 100_000:
                        raise IntegrityError("note cloud decision limit")
                    value = json.loads(row["value"])
                    projected = {key: value.get(key) for key in ("entity", "moc", "state")}
                    pair = (projected["entity"], projected["moc"])
                    if pair in decision_pairs and decision_pairs[pair] != projected:
                        raise IntegrityError("note cloud conflicting membership decisions")
                    decision_pairs[pair] = projected
                    decisions[row["id"]] = projected
            if "entities" in names:
                for number, row in enumerate(
                    side.execute(
                        "SELECT id,json_extract(value,'$.path'),json_extract(value,'$.title'),"
                        "json_extract(value,'$.sha256'),json_extract(value,'$.file_key'),"
                        "json_extract(value,'$._inventory_identity') "
                        "FROM entities WHERE vault=? ORDER BY id",
                        (vault,),
                    )
                ):
                    if number >= 20_000:
                        raise IntegrityError("note cloud history metadata limit")
                    titles[row[0]] = {
                        "path": row[1],
                        "title": row[2],
                        "sha256": row[3],
                        "file_key": json.loads(row[4] or "null"),
                        "identity": row[5],
                    }
    token = fingerprint(
        [
            [asdict(value) for value in registrations.values()],
            {key: asdict(value) for key, value in records.items()},
            proof,
            decisions,
            titles,
            history,
        ]
    )
    return Ledger(
        registrations,
        records,
        plans,
        frozenset(pending),
        pending_plans,
        decisions,
        titles,
        history,
        token,
    )
