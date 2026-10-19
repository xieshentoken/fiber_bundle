"""Withdraw only an unchanged system association proved to have been added by this batch."""

import json
from collections.abc import Callable
from dataclasses import asdict, replace
from typing import Any

from . import moc_store, note_changes, note_transfer_mocs
from . import publication_organization_store as organization
from .config import Config, IntegrityError


def freeze(
    config: Config, vid: str, identity: str, runs: list[str], records: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], bool]:
    changes = []
    protected = False
    for mid, raw in sorted(records.items()):
        if identity not in raw["members"]:
            continue
        if raw["members"][identity] != ["system"] or organization.claimed_elsewhere(
            config, vid, identity.removeprefix("publication:"), mid, runs
        ):
            protected = True
            continue
        owned = False
        for rid in runs:
            intent = organization.load(config, rid, mid)
            if intent is None:
                continue
            before = intent["before"]
            if (
                intent["vault_id"] == vid
                and "publication:" + intent["publication_id"] == identity
                and (before is None or "system" not in before["members"].get(identity, []))
                and organization.proved(config, intent)
            ):
                with moc_store.connection(config) as db:
                    assert db is not None
                    events = db.execute(
                        "SELECT record_json FROM events WHERE vault_id=? AND moc_id=? "
                        "AND revision>? ORDER BY revision LIMIT 1001",
                        (vid, mid, intent["after"]["revision"]),
                    ).fetchall()

                # Unrelated links may be added later. Any removal/manual claim ends this
                # contribution's ownership, even if a later operation restores its bytes.
                if len(events) <= 1000 and all(
                    note_transfer_mocs.record(json.loads(e[0])).members.get(identity) == ["system"]
                    for e in events
                ):
                    owned = True
                    break
        if not owned:
            protected = True
            continue
        before_record = note_transfer_mocs.record(raw)
        members = {k: v.copy() for k, v in before_record.members.items()}
        del members[identity]
        after = replace(before_record, members=members, revision=before_record.revision + 1)
        changes.append({"moc_id": mid, "before": raw, "after": asdict(after)})
    return changes, protected


def sequence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Freeze deterministic successive CAS deltas when several notes share one MOC."""
    records: dict[str, dict[str, Any]] = {}
    result = []
    for item in items:
        changes = []
        for change in item.get("moc_changes", []):
            mid = change["moc_id"]
            before = records.get(mid, change["before"])
            value = note_transfer_mocs.record(before)
            members = {k: v.copy() for k, v in value.members.items()}
            members.pop("publication:" + item["publication_id"])
            after = asdict(replace(value, members=members, revision=value.revision + 1))
            records[mid] = after
            changes.append({"moc_id": mid, "before": before, "after": after})
        result.append({**item, "moc_changes": changes})
    return result


def validate(changes: Any, publication: str) -> None:
    if not isinstance(changes, list) or len(changes) > 1000:
        raise IntegrityError("invalid revocation MOC scope")
    seen = set()
    identity = "publication:" + publication
    for change in changes:
        if not isinstance(change, dict) or set(change) != {"moc_id", "before", "after"}:
            raise IntegrityError("invalid revocation MOC change")
        note_changes.identifier(change["moc_id"])
        if change["moc_id"] in seen:
            raise IntegrityError("duplicate revocation MOC")
        seen.add(change["moc_id"])
        before, after = [note_transfer_mocs.record(change[k]) for k in ("before", "after")]
        if before.members.get(identity) != ["system"]:
            raise IntegrityError("revocation cannot remove manual MOC ownership")
        members = {k: v.copy() for k, v in before.members.items()}
        del members[identity]
        if after != replace(before, members=members, revision=before.revision + 1):
            raise IntegrityError("revocation would alter unrelated MOC ownership")


def check(config: Config, vid: str, item: dict[str, Any], *, links_only: bool = False) -> None:
    changes = {c["moc_id"]: c for c in item.get("moc_changes", [])}
    with moc_store.connection(config) as db:
        ids = (
            []
            if db is None
            else [
                r[0]
                for r in db.execute(
                    "SELECT id FROM mocs WHERE vault_id=? ORDER BY id LIMIT 1001", (vid,)
                )
            ]
        )
    if len(ids) > 1000:
        raise IntegrityError("revocation MOC limit exceeded")
    identity = "publication:" + item["publication_id"]
    for mid in ids:
        record = moc_store.load(config, vid, mid)
        assert record is not None
        change = changes.get(mid)
        if change is None:
            if not links_only and identity in record.members:
                raise IntegrityError("revocation MOC ownership was added or shared")
        elif asdict(record) not in (change["before"], change["after"]):
            raise IntegrityError("revocation MOC ownership changed")
    if set(changes) - set(ids):
        raise IntegrityError("revocation MOC disappeared")


def apply(
    config: Config,
    vid: str,
    client: str,
    batch: str,
    item: dict[str, Any],
    *,
    guard: Callable[[], None],
    links_only: bool = False,
) -> None:
    for change in item.get("moc_changes", []):
        before, after = [note_transfer_mocs.record(change[k]) for k in ("before", "after")]
        current = moc_store.load(config, vid, change["moc_id"])
        if current == after:
            with moc_store.connection(config) as db:
                assert db is not None
                event = db.execute(
                    "SELECT action,client_id FROM events WHERE vault_id=? "
                    "AND moc_id=? AND revision=?",
                    (vid, change["moc_id"], after.revision),
                ).fetchone()
            if event is None or tuple(event) != ("batch-revoke:" + batch, client):
                raise IntegrityError("revocation MOC outcome has no matching audit")
            continue
        if current != before:
            raise IntegrityError("revocation MOC revision conflict")
        check(config, vid, item, links_only=links_only)
        guard()
        moc_store.save(
            config, vid, change["moc_id"], client, after, "batch-revoke:" + batch, before.revision
        )
