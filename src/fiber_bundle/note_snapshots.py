"""Explicit scoped note metadata snapshots; immutable local files, no note writes or model calls."""

import hashlib
import json
import os
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import auth, database, note_changes, note_inventory, vaults
from .answer_model_output import reject_constant, unique_object
from .article_capture import read_bounded
from .articles import ArticleError
from .config import Config, IntegrityError, InvalidInput, contained, make_directory, sync_directory
from .inputs import canonical


def root_for(config: Config, db: sqlite3.Connection, client_id: str, vault_id: str) -> Path:
    note_changes.identifier(client_id)
    note_changes.identifier(vault_id)
    return vaults.checked_vault(config, auth.grant(db, client_id, vault_id, "query"))


def observe(
    config: Config, db: sqlite3.Connection, client_id: str, vault_id: str
) -> tuple[Path, tuple[note_changes.Entry, ...], tuple[int, int]]:
    """Two matching bounded reads detect observed races, not an atomic filesystem snapshot."""
    root = root_for(config, db, client_id, vault_id)
    info = root.stat()
    previous = None
    for _ in range(2):
        vault = auth.grant(db, client_id, vault_id, "query")
        inventory = note_inventory.read(config, db, vault)
        current = note_changes.entries(inventory.notes)
        if previous is not None and current != previous:
            raise IntegrityError("notes changed during snapshot observation")
        previous = current
    checked = root_for(config, db, client_id, vault_id).stat()
    if (checked.st_dev, checked.st_ino) != (info.st_dev, info.st_ino):
        raise IntegrityError("note snapshot vault identity changed")
    return root, current, (info.st_dev, info.st_ino)


def check_root(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    vault_id: str,
    expected: tuple[int, int],
) -> None:
    info = root_for(config, db, client_id, vault_id).stat()
    if (info.st_dev, info.st_ino) != expected:
        raise IntegrityError("note snapshot vault identity changed")


def path_for(config: Config, client_id: str, vault_id: str, identity: str) -> Path:
    note_changes.identifier(client_id)
    note_changes.identifier(vault_id)
    if not note_changes.DIGEST.fullmatch(identity):
        raise InvalidInput("invalid snapshot digest")
    return contained(config.state_root, f"note-snapshots/{client_id}/{vault_id}/{identity}.json")


def capture_locked(
    config: Config, db: sqlite3.Connection, client_id: str, vault_id: str
) -> dict[str, Any]:
    _, notes, expected = observe(config, db, client_id, vault_id)
    record = {
        "schema": note_changes.SCHEMA,
        "client_id": client_id,
        "vault_id": vault_id,
        "vault_dev": expected[0],
        "vault_ino": expected[1],
        "notes": [asdict(note) for note in notes],
    }
    note_changes.validate(record["notes"])
    data = canonical(record).encode()
    if len(data) > note_changes.MAX_BYTES:
        raise IntegrityError("note snapshot metadata byte limit exceeded")
    identity = hashlib.sha256(data).hexdigest()
    path = path_for(config, client_id, vault_id, identity)
    check_root(config, db, client_id, vault_id, expected)
    if path.exists():
        try:
            existing = read_bounded(path.parent, path.name, note_changes.MAX_BYTES)
        except (OSError, ArticleError) as exc:
            raise IntegrityError("existing note snapshot unavailable or invalid") from exc
        if existing != data:
            raise IntegrityError("existing note snapshot changed")
    else:
        for relative in [
            "note-snapshots",
            f"note-snapshots/{client_id}",
            f"note-snapshots/{client_id}/{vault_id}",
        ]:
            make_directory(contained(config.state_root, relative))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        sync_directory(path.parent)
    check_root(config, db, client_id, vault_id, expected)
    return {
        "snapshot_id": identity,
        "vault_id": vault_id,
        "note_count": len(notes),
        "metadata_only": True,
    }


def load(
    config: Config, db: sqlite3.Connection, client_id: str, vault_id: str, identity: str
) -> tuple[tuple[note_changes.Entry, ...], tuple[int, int]]:
    root = root_for(config, db, client_id, vault_id)
    path = path_for(config, client_id, vault_id, identity)
    try:
        data = read_bounded(path.parent, path.name, note_changes.MAX_BYTES)
        if hashlib.sha256(data).hexdigest() != identity:
            raise IntegrityError("note snapshot digest mismatch")
        record = json.loads(data, object_pairs_hook=unique_object, parse_constant=reject_constant)
        if not isinstance(record, dict) or set(record) != {
            "schema",
            "client_id",
            "vault_id",
            "vault_dev",
            "vault_ino",
            "notes",
        }:
            raise IntegrityError("invalid note snapshot record")
        info = root.stat()
        if (
            record["schema"] != note_changes.SCHEMA
            or record["client_id"] != client_id
            or record["vault_id"] != vault_id
            or type(record["vault_dev"]) is not int
            or type(record["vault_ino"]) is not int
            or (record["vault_dev"], record["vault_ino"]) != (info.st_dev, info.st_ino)
        ):
            raise IntegrityError("note snapshot scope or vault identity mismatch")
        result = note_changes.validate(record["notes"])
        if canonical(record).encode() != data:
            raise IntegrityError("noncanonical note snapshot record")
    except (OSError, ArticleError, ValueError, RecursionError) as exc:
        raise IntegrityError("note snapshot unavailable or invalid") from exc
    expected = (record["vault_dev"], record["vault_ino"])
    check_root(config, db, client_id, vault_id, expected)
    return result, expected


def compare_locked(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    vault_id: str,
    identity: str,
    *,
    note_identity: str | None = None,
) -> dict[str, Any]:
    baseline, expected = load(config, db, client_id, vault_id, identity)
    _, current, observed = observe(config, db, client_id, vault_id)
    if observed != expected:
        raise IntegrityError("note snapshot vault identity changed")
    if note_identity is not None:
        result = note_changes.check(baseline, current, note_identity)
    else:
        changes = note_changes.compare(baseline, current)
        result = {
            "status": "changed" if changes else "unchanged",
            "changes": changes,
            "baseline_note_count": len(baseline),
            "current_note_count": len(current),
            "write_performed": False,
        }
    check_root(config, db, client_id, vault_id, expected)
    return {"snapshot_id": identity, "vault_id": vault_id, **result}


def run(
    config: Config,
    authorization: str,
    vault_id: str,
    action: str,
    *,
    snapshot_id: str | None = None,
    note_identity: str | None = None,
) -> dict[str, Any]:
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            client_id = auth.client(db, authorization)["id"]
            if action == "capture" and snapshot_id is None and note_identity is None:
                return capture_locked(config, db, client_id, vault_id)
            if action in {"compare", "check"} and snapshot_id is not None:
                if (action == "check") != (note_identity is not None):
                    raise InvalidInput("invalid note snapshot check arguments")
                return compare_locked(
                    config, db, client_id, vault_id, snapshot_id, note_identity=note_identity
                )
            raise InvalidInput("invalid note snapshot action")
        finally:
            db.close()
