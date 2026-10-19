"""Durable publication and explicit MOC membership gates for scheduled file plans."""

import hashlib
import sqlite3
from typing import Any

from . import (
    article_publication,
    auth,
    execution_ledger,
    moc_dispatch_store,
    moc_markdown,
    moc_store,
    note_inventory,
    vaults,
)
from .config import Config, IntegrityError
from .inputs import canonical


def authorize(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    request: dict[str, Any],
    *,
    recovery: bool = False,
) -> None:
    moc_dispatch_store.validate_request(request)
    if request["client_id"] != client:
        raise auth.Denied(404)
    if recovery:
        # Recovery only confirms a previously claimed file effect or repairs its
        # identity. Cancellation must not force a file replay or strand identity.
        run = execution_ledger.get_run(db, request["run_id"])
        if run["client_id"] != client:
            raise auth.Denied(404)
        auth.grant(db, client, request["vault_id"], "add")
        vaults.checked_vault(config, auth.grant(db, client, request["vault_id"], "query"))
    else:
        run, _, _ = article_publication.bound(config, db, client, request["run_id"])
    row = db.execute(
        "SELECT p.* FROM article_publications p JOIN article_publication_runs r "
        "ON r.publication_id=p.id WHERE r.run_id=?",
        (request["run_id"],),
    ).fetchone()
    if (
        run["vault_id"] != request["vault_id"]
        or row is None
        or row["id"] != request["publication_id"]
        or row["state"] != "published"
        or row["vault_id"] != request["vault_id"]
    ):
        raise IntegrityError("MOC dispatch publication binding changed")
    record = moc_store.load(config, request["vault_id"], request["moc_id"])
    if record is None or (not recovery and "publication:" + row["id"] not in record.members):
        raise IntegrityError("MOC dispatch requires an explicit publication association")


def validate_plan(value: dict[str, Any]) -> None:
    request = value["dispatch_source"]
    moc_dispatch_store.validate_request(request)
    for field, name in [
        ("vault_id", "vault_id"),
        ("moc_id", "moc_id"),
        ("revision", "revision"),
        ("path", "path"),
        ("expected_sha256", "before_sha256"),
    ]:
        if request[field] != value[name]:
            raise IntegrityError("MOC dispatch plan binding mismatch")


def observed(
    inventory: note_inventory.Inventory,
    relative: str,
    record: moc_store.Record,
    data: bytes,
    moc_id: str,
    projections: dict[str, Any] | None = None,
) -> str:
    """Freeze member evidence and human link resolution, not unrelated MOC bodies."""
    from .organization.note_moc_graph import baseline

    notes = [
        baseline(n, (projections or {}).get(n.identity or "publication:" + str(n.publication_id)))
        for n in inventory.notes
    ]
    rows = [
        (n.relative_path, n.sha256, n.identity, n.publication_id, n.file_key)
        for n in sorted(notes, key=lambda n: n.relative_path)
        if n.relative_path != relative
        and (n.identity or ("publication:" + n.publication_id if n.publication_id else ""))
        in record.members
    ]
    prefix, _, suffix = moc_markdown.split(data, moc_id)
    manual = sorted(moc_markdown.manual_paths(prefix + suffix, inventory))
    return hashlib.sha256(canonical({"members": rows, "manual_paths": manual}).encode()).hexdigest()
