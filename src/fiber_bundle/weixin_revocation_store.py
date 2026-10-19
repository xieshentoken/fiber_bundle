"""Frozen account-bound batch cleanup scope and append-only phase audit."""

import hashlib
import json
import math
import sqlite3
from dataclasses import asdict
from typing import Any

from . import batch_moc_contributions, database, note_changes, note_migration_plans, weixin_inbox
from .config import IntegrityError, InvalidInput
from .inputs import canonical
from .weixin_events import Binding

REASONS = {
    "eligible",
    "location_or_state_changed",
    "existing_or_unproven",
    "shared_or_associated",
    "already_missing",
    "human_modified_or_replaced",
}
PHASES = (REASONS - {"eligible"}) | {
    "planned",
    "claimed",
    "links_claimed",
    "links_revoked",
    "revoked",
    "not_revoked",
    "evidence_changed",
}
FIELDS = {"publication_id", "reason", "note_path", "sha256", "file_key", "vault_key", "parent_key"}


def validate(item: Any) -> None:
    if (
        not isinstance(item, dict)
        or set(item) not in (FIELDS, FIELDS | {"moc_changes"})
        or item["reason"] not in REASONS
    ):
        raise IntegrityError("invalid revocation item")
    note_changes.identifier(item["publication_id"])
    batch_moc_contributions.validate(item.get("moc_changes", []), item["publication_id"])
    if item["note_path"] is not None:
        note_migration_plans.note_path(item["note_path"])
    if item["sha256"] is not None and (
        not isinstance(item["sha256"], str) or not note_changes.DIGEST.fullmatch(item["sha256"])
    ):
        raise IntegrityError("invalid revocation note digest")
    for name in ("file_key", "vault_key", "parent_key"):
        value = item[name]
        if value is None and name != "vault_key" and item["reason"] != "eligible":
            continue
        if (
            not isinstance(value, list)
            or len(value) != (3 if name == "file_key" else 2)
            or any(type(v) is not int or v < 0 for v in value[:2])
        ):
            raise IntegrityError("invalid revocation physical identity")
    if item["file_key"] is not None:
        try:
            birth = float.fromhex(item["file_key"][2])
            if not math.isfinite(birth) or birth.hex() != item["file_key"][2]:
                raise ValueError("invalid birth")
        except (ValueError, TypeError) as exc:
            raise IntegrityError("invalid revocation physical identity") from exc
    if item["reason"] == "eligible" and (item["sha256"] is None or item["note_path"] is None):
        raise IntegrityError("eligible revocation missing evidence")


def save(
    journal: sqlite3.Connection, binding: Binding, batch: str, values: list[dict[str, Any]]
) -> None:
    weixin_inbox.bound(journal, binding)
    if len(values) > 100 or [v["publication_id"] for v in values] != sorted(
        {v["publication_id"] for v in values}
    ):
        raise IntegrityError("revocation publication scope limit or order")
    for value in values:
        validate(value)
    raw = canonical({"schema": 1, "binding": asdict(binding), "items": values})
    if len(raw.encode()) > 512 * 1024:
        raise IntegrityError("revocation plan metadata limit exceeded")
    with database.transaction(journal):
        journal.execute(
            "CREATE TABLE IF NOT EXISTS wx_revocation_batches("
            "batch_id TEXT PRIMARY KEY,intent_json TEXT NOT NULL,"
            "intent_sha256 TEXT NOT NULL) STRICT"
        )
        journal.execute(
            "CREATE TABLE IF NOT EXISTS wx_batch_revocations("
            "batch_id TEXT NOT NULL,publication_id TEXT NOT NULL,intent_json TEXT NOT NULL,"
            "intent_sha256 TEXT NOT NULL,phase TEXT NOT NULL,"
            "PRIMARY KEY(batch_id,publication_id)) STRICT"
        )
        journal.execute(
            "CREATE TABLE IF NOT EXISTS wx_revocation_events("
            "id INTEGER PRIMARY KEY,batch_id TEXT NOT NULL,publication_id TEXT NOT NULL,"
            "phase TEXT NOT NULL) STRICT"
        )
        if journal.execute(
            "SELECT 1 FROM wx_revocation_batches WHERE batch_id=?", (batch,)
        ).fetchone():
            return  # Existing fixed scope is never re-planned, even after file effects.
        if journal.execute("SELECT count(*) FROM wx_revocation_batches").fetchone()[0] >= 1000:
            raise IntegrityError("revocation batch plan limit exceeded")
        journal.execute(
            "INSERT INTO wx_revocation_batches VALUES(?,?,?)",
            (batch, raw, hashlib.sha256(raw.encode()).hexdigest()),
        )
        for item in values:
            encoded = canonical(item)
            phase = (
                "planned"
                if item["reason"] == "eligible" or item.get("moc_changes")
                else item["reason"]
            )
            journal.execute(
                "INSERT INTO wx_batch_revocations VALUES(?,?,?,?,?)",
                (
                    batch,
                    item["publication_id"],
                    encoded,
                    hashlib.sha256(encoded.encode()).hexdigest(),
                    phase,
                ),
            )
            journal.execute(
                "INSERT INTO wx_revocation_events(batch_id,publication_id,phase) VALUES(?,?,?)",
                (batch, item["publication_id"], phase),
            )


def load(
    journal: sqlite3.Connection, binding: Binding, batch: str
) -> list[tuple[dict[str, Any], str]]:
    weixin_inbox.bound(journal, binding)
    if not journal.execute(
        "SELECT 1 FROM sqlite_master WHERE name='wx_revocation_batches'"
    ).fetchone():
        raise InvalidInput("revocation requires an explicit batch plan")
    plan = journal.execute(
        "SELECT * FROM wx_revocation_batches WHERE batch_id=?", (batch,)
    ).fetchone()
    if plan is None:
        raise InvalidInput("revocation requires an explicit batch plan")
    raw = plan["intent_json"]
    if (
        len(raw.encode()) > 512 * 1024
        or hashlib.sha256(raw.encode()).hexdigest() != plan["intent_sha256"]
    ):
        raise IntegrityError("revocation scope digest changed")
    value = json.loads(raw)
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "binding", "items"}
        or type(value["schema"]) is not int
        or value["schema"] != 1
        or value["binding"] != asdict(binding)
        or canonical(value) != raw
        or not isinstance(value["items"], list)
        or len(value["items"]) > 100
    ):
        raise IntegrityError("invalid revocation scope")
    result = []
    saved = journal.execute(
        "SELECT * FROM wx_batch_revocations WHERE batch_id=? ORDER BY publication_id LIMIT 101",
        (batch,),
    ).fetchall()
    if len(saved) != len(value["items"]):
        raise IntegrityError("revocation item scope incomplete")
    for item, row in zip(value["items"], saved, strict=True):
        validate(item)
        encoded = canonical(item)
        event = journal.execute(
            "SELECT phase FROM wx_revocation_events WHERE batch_id=? AND publication_id=? "
            "ORDER BY id DESC LIMIT 1",
            (batch, item["publication_id"]),
        ).fetchone()
        if (
            row["publication_id"] != item["publication_id"]
            or row["intent_json"] != encoded
            or row["intent_sha256"] != hashlib.sha256(encoded.encode()).hexdigest()
            or row["phase"] not in PHASES
            or event is None
            or event[0] != row["phase"]
        ):
            raise IntegrityError("revocation item or phase audit changed")
        result.append((item, row["phase"]))
    return result


def transition(
    journal: sqlite3.Connection, batch: str, publication: str, before: str, after: str
) -> None:
    if before not in PHASES or after not in PHASES:
        raise IntegrityError("invalid revocation phase")
    with database.transaction(journal):
        if (
            journal.execute(
                "UPDATE wx_batch_revocations SET phase=? "
                "WHERE batch_id=? AND publication_id=? AND phase=?",
                (after, batch, publication, before),
            ).rowcount
            != 1
        ):
            raise IntegrityError("revocation phase conflict")
        journal.execute(
            "INSERT INTO wx_revocation_events(batch_id,publication_id,phase) VALUES(?,?,?)",
            (batch, publication, after),
        )
