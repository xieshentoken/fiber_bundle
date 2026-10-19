"""Immutable publication-to-MOC selections and contribution evidence in the MOC ledger."""

import hashlib
import json
from typing import Any

from . import database, moc_store, note_changes, note_transfer_mocs
from .config import Config, IntegrityError
from .inputs import canonical

FIELDS = {
    "schema",
    "client_id",
    "run_id",
    "vault_id",
    "publication_id",
    "moc_id",
    "path",
    "expected_sha256",
    "before",
    "after",
}


def validate(value: Any) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != FIELDS
        or type(value["schema"]) is not int
        or value["schema"] != 1
    ):
        raise IntegrityError("invalid publication organization intent")
    for field in ("client_id", "run_id", "vault_id", "publication_id", "moc_id"):
        note_changes.identifier(value[field])
    from . import moc_dispatch_store

    after = note_transfer_mocs.record(value["after"])
    before = note_transfer_mocs.record(value["before"]) if value["before"] is not None else None
    members = {} if before is None else {k: v.copy() for k, v in before.members.items()}
    identity = "publication:" + value["publication_id"]
    members[identity] = sorted(set(members.get(identity, [])) | {"system"})
    from dataclasses import replace

    if before is not None:
        revision = before.revision + (members != before.members)
        if after != replace(before, members=members, revision=revision):
            raise IntegrityError("organization would change unrelated MOC ownership")
    elif after.revision != 1 or after.members != members:
        raise IntegrityError("new organization MOC has unrelated ownership")
    moc_dispatch_store.validate_request(
        {
            "request_id": value["run_id"],
            "client_id": value["client_id"],
            "vault_id": value["vault_id"],
            "run_id": value["run_id"],
            "publication_id": value["publication_id"],
            "moc_id": value["moc_id"],
            "revision": after.revision,
            "path": value["path"],
            "expected_sha256": value["expected_sha256"],
        }
    )


def load(config: Config, run: str, mid: str) -> dict[str, Any] | None:
    with moc_store.connection(config) as db:
        if (
            db is None
            or not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='publication_organization'"
            ).fetchone()
        ):
            return None
        row = db.execute(
            "SELECT * FROM publication_organization WHERE run_id=? AND moc_id=?", (run, mid)
        ).fetchone()
    if row is None:
        return None
    raw = row["intent_json"]
    if len(raw.encode()) > 512 * 1024 or hashlib.sha256(raw.encode()).hexdigest() != row["sha256"]:
        raise IntegrityError("organization intent changed")
    value = json.loads(raw)
    validate(value)
    if canonical(value) != raw or (value["run_id"], value["moc_id"]) != (run, mid):
        raise IntegrityError("organization scope changed")
    return dict(value)


def save(config: Config, value: dict[str, Any]) -> None:
    validate(value)
    raw = canonical(value)
    with moc_store.connection(config, write=True) as db:
        assert db is not None
        with database.transaction(db):
            db.execute(
                "CREATE TABLE IF NOT EXISTS publication_organization("
                "run_id TEXT NOT NULL,moc_id TEXT NOT NULL,intent_json TEXT NOT NULL,"
                "sha256 TEXT NOT NULL,PRIMARY KEY(run_id,moc_id)) STRICT"
            )
            existing = db.execute(
                "SELECT intent_json FROM publication_organization WHERE run_id=? AND moc_id=?",
                (value["run_id"], value["moc_id"]),
            ).fetchone()
            if existing is not None:
                if existing[0] != raw:
                    raise IntegrityError("organization selection already frozen")
                return
            if db.execute("SELECT count(*) FROM publication_organization").fetchone()[0] >= 1000:
                raise IntegrityError("organization intent limit exceeded")
            db.execute(
                "INSERT INTO publication_organization VALUES(?,?,?,?)",
                (value["run_id"], value["moc_id"], raw, hashlib.sha256(raw.encode()).hexdigest()),
            )


def proved(config: Config, value: dict[str, Any]) -> bool:
    """Only this run's atomic MOC audit can prove a newly added contribution."""
    if value["before"] == value["after"]:
        return True
    with moc_store.connection(config) as db:
        if db is None:
            return False
        row = db.execute(
            "SELECT * FROM events WHERE vault_id=? AND moc_id=? AND revision=?",
            (value["vault_id"], value["moc_id"], value["after"]["revision"]),
        ).fetchone()
    return row is not None and (row["client_id"], row["action"], row["record_json"]) == (
        value["client_id"],
        "publication-organize:" + value["run_id"],
        canonical(value["after"]),
    )


def claimed_elsewhere(
    config: Config, vid: str, publication: str, mid: str, runs: list[str]
) -> bool:
    """A second explicit selection protects even an association that was already present."""
    with moc_store.connection(config) as db:
        if (
            db is None
            or not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='publication_organization'"
            ).fetchone()
        ):
            return False
        rows = db.execute(
            "SELECT run_id FROM publication_organization WHERE moc_id=? LIMIT 1001", (mid,)
        ).fetchall()
    if len(rows) > 1000:
        raise IntegrityError("organization claim limit exceeded")
    for row in rows:
        if row[0] in runs:
            continue
        intent = load(config, row[0], mid)
        assert intent is not None
        if (
            intent["vault_id"] == vid
            and intent["publication_id"] == publication
            and proved(config, intent)
        ):
            return True
    return False
