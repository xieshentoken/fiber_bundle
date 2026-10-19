"""One planned historical MOC identity transition; never suppress other registrations."""

import math
import sqlite3
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from . import auth, note_identities, note_identity_store, note_index, note_inventory, vaults
from .config import Config, IntegrityError
from .note_identity_store import Registration


def file_key(value: Any) -> tuple[int, int, str]:
    if (
        not isinstance(value, list)
        or len(value) != 3
        or any(type(v) is not int or v < 0 for v in value[:2])
        or not isinstance(value[2], str)
    ):
        raise IntegrityError("invalid MOC physical identity")
    try:
        birth = float.fromhex(value[2])
        if not math.isfinite(birth) or birth.hex() != value[2]:
            raise ValueError("invalid birth time")
    except ValueError as exc:
        raise IntegrityError("invalid MOC file birth") from exc
    return value[0], value[1], value[2]


def decode(value: Any) -> Registration:
    if not isinstance(value, dict) or set(value) != set(Registration.__dataclass_fields__):
        raise IntegrityError("invalid MOC registered identity")
    key = value["vault_key"]
    if not isinstance(key, list) or len(key) != 2 or any(type(v) is not int or v < 0 for v in key):
        raise IntegrityError("invalid MOC identity Vault")
    record = Registration(
        **{**value, "file_key": file_key(value["file_key"]), "vault_key": tuple(key)}
    )
    note_identities.validate(record)
    if record.publication_id is not None:
        raise IntegrityError("MOC cannot update a publication identity")
    return record


def transition(value: dict[str, Any]) -> tuple[Registration, Registration] | None:
    if value["schema"] == 1 or (value["schema"] in {3, 4} and value["identity"] is None):
        return None
    before = decode(value["identity"])
    after_key = file_key(value["after_file_key"])
    if (
        value["before_sha256"] == "absent"
        or list(before.file_key[:2]) != value["file_key"]
        or list(before.vault_key) != value["vault_key"]
        or after_key == before.file_key
        or after_key[0] != before.file_key[0]
    ):
        raise IntegrityError("MOC identity plan binding mismatch")
    return before, replace(
        before,
        relative_path=value["path"],
        sha256=value["after_sha256"],
        file_key=after_key,
        revision=before.revision + 1,
    )


def capture(
    config: Config, vid: str, inventory: note_inventory.Inventory, relative: str
) -> dict[str, Any] | None:
    note = next((n for n in inventory.notes if n.relative_path == relative), None)
    if note is None or note.identity is None:
        return None
    record = next(
        (r for r in note_identity_store.records(config, vid) if r.identity == note.identity), None
    )
    if record is None or record.file_key != note.file_key or record.publication_id is not None:
        raise IntegrityError("MOC identity changed during observation")
    # Canonical JSON converts tuple keys to lists in the persisted plan.
    return {
        **asdict(record),
        "file_key": list(record.file_key),
        "vault_key": list(record.vault_key),
    }


def check_moved_target(
    inventory: note_inventory.Inventory,
    relative: str,
    prior: dict[str, Any],
    *,
    applied: dict[str, Any] | None = None,
) -> None:
    """Accept an external move only of the last applied registered physical file."""
    pair = transition(prior)
    note = next((n for n in inventory.notes if n.relative_path == relative), None)
    if pair is None or note is None:
        raise IntegrityError("MOC path move requires an applied registered target")
    after = pair[1]
    if applied is not None:
        updated = transition(applied)
        if updated is None or updated[0].identity != after.identity:
            raise IntegrityError("MOC moved plan identity changed")
        after = updated[1]
    if (note.identity, note.file_key, note.publication_id) != (
        after.identity,
        after.file_key,
        None,
    ):
        raise IntegrityError("MOC path move physical identity changed")
    # Never adopt a second target while the old path is occupied, even if its
    # registration has since been explicitly rebound to another location.
    if any(n.relative_path == prior["path"] for n in inventory.notes):
        raise IntegrityError("MOC previous target path is still occupied")


def current(config: Config, vid: str, identity: str) -> Registration | None:
    return next(
        (r for r in note_identity_store.records(config, vid) if r.identity == identity), None
    )


def projection(
    config: Config, vid: str, root: Path, value: dict[str, Any], *, allow_after: bool
) -> tuple[Registration, Registration] | None:
    pair = transition(value)
    if pair is None:
        return None
    before, after = pair
    saved = current(config, vid, before.identity)
    if allow_after:
        try:
            note = note_index.read_note(root, value["path"], note_index.Limits().note_bytes)
        except FileNotFoundError:
            note = None
        if note is not None and (note.sha256, note.file_key) == (after.sha256, after.file_key):
            if saved not in pair:
                raise IntegrityError("MOC registered identity changed; preserve pending write")
            return pair if saved == before else None
    if saved != before:
        raise IntegrityError("MOC registered identity changed")
    return None


def check_target(
    inventory: note_inventory.Inventory, value: dict[str, Any], *, allow_after: bool = False
) -> None:
    pair = transition(value)
    note = next((n for n in inventory.notes if n.relative_path == value["path"]), None)
    if pair is None:
        if note is not None and (note.identity is not None or note.publication_id is not None):
            raise IntegrityError("MOC plan did not authorize a registered target")
        if value["schema"] in {3, 4} and note is not None and note.sha256 == value["after_sha256"]:
            if note.file_key != file_key(value["after_file_key"]):
                raise IntegrityError("MOC dispatch candidate physical identity changed")
        return
    allowed = pair if allow_after else pair[:1]
    if note is None or not any(
        (note.identity, note.file_key) == (expected.identity, expected.file_key)
        for expected in allowed
    ):
        raise IntegrityError("MOC target physical identity changed")


def candidate_matches(path: Path, value: dict[str, Any], *, before: bool) -> bool:
    pair = transition(value)
    if pair is None:
        if value["schema"] in {3, 4} and not before:
            note = note_index.read_note(path, "candidate.md", note_index.Limits().note_bytes)
            return (note.sha256, note.file_key) == (
                value["after_sha256"],
                file_key(value["after_file_key"]),
            )
        return True
    note = note_index.read_note(path, "candidate.md", note_index.Limits().note_bytes)
    expected = pair[0 if before else 1]
    digest = value["before_sha256"] if before else value["after_sha256"]
    return (note.sha256, note.file_key) == (digest, expected.file_key)


def unchanged(config: Config, vid: str, value: dict[str, Any]) -> bool:
    pair = transition(value)
    return pair is None or current(config, vid, pair[0].identity) == pair[0]


def complete(
    config: Config, db: sqlite3.Connection, vid: str, client: str, root: Path, value: dict[str, Any]
) -> None:
    pair = transition(value)
    if pair is None:
        return
    before, after = pair
    note = note_index.read_note(root, value["path"], note_index.Limits().note_bytes)
    if (note.sha256, note.file_key) != (after.sha256, after.file_key):
        raise IntegrityError("MOC changed before identity commit")
    auth.grant(db, client, vid, "add")
    info = vaults.checked_vault(config, auth.grant(db, client, vid, "query")).stat()
    parent = (root / value["path"]).parent.stat()
    if (info.st_dev, info.st_ino) != before.vault_key or [parent.st_dev, parent.st_ino] != value[
        "parent_key"
    ]:
        raise IntegrityError("MOC filesystem changed before identity commit")
    saved = current(config, vid, before.identity)
    if saved == after:
        return
    if saved != before:
        raise IntegrityError("MOC identity revision conflict")
    note_identity_store.save(config, vid, client, after, expected_revision=before.revision)
