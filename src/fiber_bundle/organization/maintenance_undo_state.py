"""Compensate only task-owned ledger differences; keep immutable audit history."""

import json
from dataclasses import asdict
from typing import Any

from .. import database, moc_store, note_identity_store
from ..config import IntegrityError
from ..inputs import canonical
from . import maintenance_undo_blobs as blobs
from . import store

ORG_TABLES = ("mocs", "decisions", "note_moc", "packages", "versions")


def snapshot(
    config: Any, db: Any, vault: str, *, persist: bool = False, compact: bool = False
) -> dict[str, Any]:
    with moc_store.connection(config) as side:
        mocs = (
            {
                r["id"]: json.loads(r["record_json"])
                for r in side.execute("SELECT * FROM mocs WHERE vault_id=?", (vault,))
            }
            if side
            else {}
        )
    org = {}
    with store.connection(config) as side:
        for table in ORG_TABLES:
            exists = (
                side
                and side.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone()
            )
            org[table] = (
                {
                    r["id"]: (
                        blobs.pack(config, {"value": r["value"]}, persist=persist)["value"]
                        if compact and len(r["value"]) >= 4096
                        else json.loads(r["value"])
                    )
                    for r in side.execute(f"SELECT id,value FROM {table} WHERE vault=?", (vault,))
                }
                if exists and side is not None
                else {}
            )
    return {
        "mocs": mocs,
        "identities": {r.identity: asdict(r) for r in note_identity_store.records(config, vault)},
        "organization": org,
        "locations": {
            canonical([r["kind"], r["publication_id"]]): blobs.pack(config, r, persist=persist)
            for r in db.execute("SELECT * FROM publication_locations WHERE vault_id=?", (vault,))
        },
    }


def materialize(config: Any, value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"undo_blob_sha256"}:
        return json.loads(blobs.unpack(config, value))
    return value


def preflight(config: Any, before: Any, after: Any) -> None:
    """Check retained payloads before restoring any file, including after a restart."""
    for key in changed(before["locations"], after["locations"]):
        for section in (before, after):
            row = section["locations"].get(key)
            if row:
                blobs.unpack(config, row["value"])
    for table in ORG_TABLES:
        for key in changed(before["organization"][table], after["organization"][table]):
            for section in (before, after):
                materialize(config, section["organization"][table].get(key))


def changed(before: Any, after: Any) -> list[str]:
    return sorted(
        k for k in set(before) | set(after) if canonical(before.get(k)) != canonical(after.get(k))
    )


def check(before: Any, after: Any, current: Any) -> None:
    for section in ("mocs", "identities", "locations"):
        for k in changed(before[section], after[section]):
            if canonical(current[section].get(k)) != canonical(after[section].get(k)):
                raise IntegrityError("maintenance_undo_relationship_conflict")
    for table in ORG_TABLES:
        for k in changed(before["organization"][table], after["organization"][table]):
            if current["organization"][table].get(k) != after["organization"][table].get(k):
                raise IntegrityError("maintenance_undo_relationship_conflict")


def refresh_unowned(before: Any, after: Any, current: Any) -> None:
    """Unrelated writes between task steps belong to their authors, not this undo."""
    check(before, after, current)
    pairs = [(before[k], after[k], current[k]) for k in ("mocs", "identities", "locations")]
    pairs += [
        (before["organization"][t], after["organization"][t], current["organization"][t])
        for t in ORG_TABLES
    ]
    for original, latest, now in pairs:
        for k in set(original) | set(latest) | set(now):
            if canonical(original.get(k)) == canonical(latest.get(k)):
                if k in now:
                    original[k] = latest[k] = now[k]
                else:
                    original.pop(k, None)
                    latest.pop(k, None)


def comparable(value: Any) -> str:
    return (
        canonical({k: v for k, v in value.items() if k != "revision"}) if value else canonical(None)
    )


def expected(current: Any, old: Any, new: Any) -> bool:
    if comparable(current) == comparable(old):
        return False  # A prior compensation completed before its progress marker.
    if comparable(current) != comparable(new):
        raise IntegrityError("maintenance_undo_relationship_conflict")
    return True


def restore(config: Any, db: Any, job: Any, journal: Any, save: Any) -> None:
    """Each section is durable; monotonic compensation revisions preserve event uniqueness."""
    before, after = journal["before_state"], journal["after_state"]
    done = journal.setdefault("state_restored", [])
    vault, client = job["vault_id"], job["client_id"]
    if "mocs" not in done:
        for mid in changed(before["mocs"], after["mocs"]):
            old = before["mocs"].get(mid)
            current = moc_store.load(config, vault, mid)
            if not expected(asdict(current) if current else None, old, after["mocs"].get(mid)):
                continue
            if old is None:
                if current is not None:
                    moc_store.retire(config, vault, mid, client, current.revision)
            elif current is None or any(
                asdict(current)[k] != old[k] for k in old if k != "revision"
            ):
                assert current is not None
                restored = moc_store.Record(**{**old, "revision": current.revision + 1})
                moc_store.save(
                    config, vault, mid, client, restored, "maintenance-cancel", current.revision
                )
        done.append("mocs")
        save()
    if "identities" not in done:
        registrations = {r.identity: r for r in note_identity_store.records(config, vault)}
        for identity in changed(before["identities"], after["identities"]):
            old = before["identities"].get(identity)
            now = registrations.get(identity)
            if not expected(asdict(now) if now else None, old, after["identities"].get(identity)):
                continue
            if old is None:
                if now is not None:
                    with note_identity_store.connection(config, write=True) as side:
                        assert side is not None
                        side.execute(
                            "DELETE FROM registrations WHERE vault_id=? "
                            "AND identity=? AND revision=?",
                            (vault, identity, now.revision),
                        )
            elif now is not None:
                registration = note_identity_store.Registration(
                    **{
                        **old,
                        "file_key": tuple(old["file_key"]),
                        "vault_key": tuple(old["vault_key"]),
                        "revision": now.revision + 1,
                    }
                )
                if any(asdict(now)[k] != asdict(registration)[k] for k in old if k != "revision"):
                    note_identity_store.save(
                        config, vault, client, registration, expected_revision=now.revision
                    )
        done.append("identities")
        save()
    if "locations" not in done:
        with database.transaction(db):
            for k in changed(before["locations"], after["locations"]):
                old, new = before["locations"].get(k), after["locations"].get(k)
                identity = old or new
                row = db.execute(
                    "SELECT * FROM publication_locations WHERE kind=? AND publication_id=?",
                    (identity["kind"], identity["publication_id"]),
                ).fetchone()
                if not expected(blobs.pack(config, row), old, new):
                    continue
                if old is None:
                    db.execute(
                        "DELETE FROM publication_locations WHERE kind=? AND publication_id=?",
                        (new["kind"], new["publication_id"]),
                    )
                else:
                    fields = [f for f in old if f not in {"kind", "publication_id", "revision"}]
                    db.execute(
                        "UPDATE publication_locations SET "
                        + ",".join(f + "=?" for f in fields)
                        + ",revision=revision+1 WHERE kind=? AND publication_id=?",
                        (
                            *[
                                blobs.unpack(config, old[f]) if f == "value" else old[f]
                                for f in fields
                            ],
                            old["kind"],
                            old["publication_id"],
                        ),
                    )
        done.append("locations")
        save()
    if "organization" not in done:
        with store.connection(config, write=True) as side:
            assert side is not None
            with side:
                for table in ORG_TABLES:
                    for k in changed(before["organization"][table], after["organization"][table]):
                        old = materialize(config, before["organization"][table].get(k))
                        current = side.execute(
                            f"SELECT value FROM {table} WHERE id=?", (k,)
                        ).fetchone()
                        current = json.loads(current[0]) if current else None
                        if current == old:
                            continue
                        if current != materialize(config, after["organization"][table].get(k)):
                            raise IntegrityError("maintenance_undo_relationship_conflict")
                        if old is None:
                            side.execute(f"DELETE FROM {table} WHERE id=? AND vault=?", (k, vault))
                        else:
                            # Existing audit timestamps are intentionally not rewound.
                            side.execute(
                                f"INSERT INTO {table} VALUES(?,?,?,0) ON CONFLICT(id) "
                                "DO UPDATE SET value=excluded.value",
                                (k, vault, canonical(old)),
                            )
        done.append("organization")
        save()
