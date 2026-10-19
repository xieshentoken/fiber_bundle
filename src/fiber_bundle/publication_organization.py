"""Explicit local incremental organization; no body rewrite or global classification."""

import sqlite3
from dataclasses import asdict, replace
from typing import Any

from . import (
    article_publication,
    auth,
    database,
    moc_dispatch,
    moc_files,
    moc_store,
    note_changes,
    note_inventory,
    note_transfer_mocs,
    vaults,
)
from . import publication_organization_store as intents
from .config import Config, IntegrityError, InvalidInput


def queue_locked(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    rid: str,
    *,
    moc_id: str,
    revision: int,
    relative: str,
    expected_sha256: str,
    new_title: str | None = None,
) -> dict[str, Any]:
    """Caller holds storage lock. Freeze selection before metadata or queue effects."""
    note_changes.identifier(moc_id)
    if type(revision) is not int or revision < 0:
        raise InvalidInput("organization requires an integer MOC revision")
    run, _, root = article_publication.bound(config, db, client, rid)
    vid = run["vault_id"]
    vault = auth.grant(db, client, vid, "query")
    row = db.execute(
        "SELECT p.* FROM article_publications p JOIN article_publication_runs r "
        "ON r.publication_id=p.id WHERE r.run_id=?",
        (rid,),
    ).fetchone()
    if row is None or row["state"] != "published" or row["vault_id"] != vid:
        raise InvalidInput("organization requires this run's visible publication")
    inventory = note_inventory.read(config, db, vault)
    if not any(n.publication_id == row["id"] for n in inventory.notes):
        raise IntegrityError("organization publication is missing")
    value = intents.load(config, rid, moc_id)
    if value is None:
        before = moc_store.load(config, vid, moc_id)
        info = root.stat()
        if before is None:
            if revision != 0 or new_title is None:
                raise InvalidInput("new MOC requires explicit title and revision zero")
            after = moc_store.Record(
                new_title, info.st_dev, info.st_ino, 1, {"publication:" + row["id"]: ["system"]}
            )
        else:
            if new_title is not None or before.revision != revision:
                raise IntegrityError("organization MOC revision conflict")
            if (before.vault_dev, before.vault_ino) != (info.st_dev, info.st_ino):
                raise IntegrityError("organization MOC root changed")
            members = {k: v.copy() for k, v in before.members.items()}
            identity = "publication:" + row["id"]
            members[identity] = sorted(set(members.get(identity, [])) | {"system"})
            after = replace(
                before, members=members, revision=before.revision + (members != before.members)
            )
        value = {
            "schema": 1,
            "client_id": client,
            "run_id": rid,
            "vault_id": vid,
            "publication_id": row["id"],
            "moc_id": moc_id,
            "path": relative,
            "expected_sha256": expected_sha256,
            "before": asdict(before) if before is not None else None,
            "after": asdict(after),
        }
        intents.validate(value)
        # Fail before associating if the requested file or managed block is already conflicting.
        data, key = moc_files.target(inventory, relative)
        if (moc_files.digest(data) if key is not None else "absent") != expected_sha256:
            raise IntegrityError("organization MOC file hash conflict")
        intents.save(config, value)
    else:
        previous = value["before"]
        if (
            value["client_id"],
            value["vault_id"],
            value["publication_id"],
            value["path"],
            value["expected_sha256"],
            previous["revision"] if previous else 0,
            value["after"]["title"] if previous is None else None,
        ) != (client, vid, row["id"], relative, expected_sha256, revision, new_title):
            raise IntegrityError("organization selection is immutable")
    before = note_transfer_mocs.record(value["before"]) if value["before"] is not None else None
    after = note_transfer_mocs.record(value["after"])
    current = moc_store.load(config, vid, moc_id)
    article_publication.bound(config, db, client, rid)
    auth.grant(db, client, vid, "query")
    info = vaults.checked_vault(config, vault).stat()
    if (info.st_dev, info.st_ino) != (after.vault_dev, after.vault_ino):
        raise IntegrityError("organization Vault identity changed")
    if current != after:
        if current != before:
            raise IntegrityError("organization metadata changed; preserve human ownership")
        moc_store.save(
            config,
            vid,
            moc_id,
            client,
            after,
            "publication-organize:" + rid,
            before.revision if before is not None else 0,
        )
    elif not intents.proved(config, value):
        raise IntegrityError("organization contribution audit missing")
    return moc_dispatch.schedule_locked(
        config,
        db,
        client,
        vid,
        rid,
        moc_id,
        revision=after.revision,
        relative=relative,
        expected_sha256=expected_sha256,
    )


def queue(config: Config, authorization: str, rid: str, **selection: Any) -> dict[str, Any]:
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            return queue_locked(config, db, auth.client(db, authorization)["id"], rid, **selection)
        finally:
            db.close()
