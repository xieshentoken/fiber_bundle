"""Explicit sidecar registration and physical-file identity resolution; no note writes."""

import os
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import (
    auth,
    database,
    note_changes,
    note_identity_store,
    note_index,
    note_publications,
    vaults,
)
from .config import Config, IntegrityError, InvalidInput, contained
from .note_identity_store import Registration


def original_in_bundle(root: Path, relative: str, key: tuple[int, int, str]) -> bool:
    total = count = 0
    limits = note_index.Limits()
    directory = contained(root, relative)
    with os.scandir(directory) as items:
        for entry in items:
            count += 1
            if count > limits.entries:
                raise IntegrityError("publication rebind entry limit exceeded")
            if (
                entry.name.startswith(".")
                or not entry.is_file(follow_symlinks=False)
                or not entry.name.lower().endswith(".md")
            ):
                continue
            note = note_index.read_note(root, relative + "/" + entry.name, limits.note_bytes)
            total += len(note.text.encode())
            if total > limits.total_bytes:
                raise IntegrityError("publication rebind byte limit exceeded")
            if note.file_key == key:
                return True
    return False


def validate(record: Registration) -> None:
    note_changes.validate(
        [{"identity": record.identity, "note_path": record.relative_path, "sha256": record.sha256}]
    )
    if record.publication_id is not None:
        note_changes.identifier(record.publication_id)
        if record.identity != "publication:" + record.publication_id:
            raise IntegrityError("invalid publication identity registration")
    elif not record.identity.startswith("note:"):
        raise IntegrityError("invalid historical identity registration")
    if (
        any(type(v) is not int or v < 0 for v in (*record.file_key[:2], *record.vault_key))
        or type(record.revision) is not int
        or record.revision < 1
    ):
        raise IntegrityError("invalid physical identity registration")


def resolve(
    notes: list[note_index.Note], records: list[Registration], vault_key: tuple[int, int]
) -> list[note_index.Note]:
    by_key = {}
    for record in records:
        validate(record)
        if record.vault_key != vault_key or record.file_key in by_key:
            raise IntegrityError("note identity vault or file conflict")
        by_key[record.file_key] = record
    found: set[str] = set()
    result = []
    for note in notes:
        matched = by_key.get(note.file_key) if note.file_key is not None else None
        if matched is None:
            result.append(note)
            continue
        if (
            matched.identity in found
            or matched.publication_id != note.publication_id
            or (note.identity is not None and note.identity != matched.identity)
        ):
            raise IntegrityError("note identity conflicts with current provenance")
        found.add(matched.identity)
        result.append(replace(note, identity=matched.identity))
    for record in records:
        if record.identity not in found and any(
            n.relative_path == record.relative_path for n in notes
        ):
            raise IntegrityError("layout_tracked_note_replaced")
    return result


def candidate(
    config: Config,
    main: sqlite3.Connection,
    vault: sqlite3.Row,
    relative: str,
    publication_id: str | None,
) -> note_index.Note:
    from . import note_inventory

    root = vaults.checked_vault(config, vault)
    inventory = note_inventory.read(config, main, vault, registered=False)
    visible = next((note for note in inventory.notes if note.relative_path == relative), None)
    if visible is not None:
        if visible.publication_id != publication_id:
            raise IntegrityError("note registration source kind changed")
        return visible
    if publication_id is None:
        raise InvalidInput("only current visible Markdown notes may be registered")
    parent = relative.rsplit("/", 1)[0]
    row = inventory.publications.get(publication_id)
    if row is None:
        raise IntegrityError("publication registration is not published")
    if inventory.locations.get(publication_id) != parent:
        raise InvalidInput("publication registration directory is not currently visible")
    data = note_publications.read_marker(root, parent, note_index.Limits().note_bytes)
    bound = note_publications.identify(data, inventory.bindings)
    if bound is None or bound.publication_id != publication_id:
        raise IntegrityError("publication rebind marker mismatch")
    # The marked directory must be reachable by the same bounded scan, including hidden rules.
    if any(part.startswith(".") for part in relative.split("/")):
        raise InvalidInput("hidden registration path")
    if len(relative.split("/")) > note_index.Limits().depth + 2:
        raise InvalidInput("registration path too deep")
    return replace(
        note_index.read_note(root, relative, note_index.Limits().note_bytes),
        publication_id=publication_id,
        publication_path=parent,
    )


def run(
    config: Config,
    authorization: str,
    vault_id: str,
    action: str,
    relative: str,
    expected_sha256: str,
    *,
    identity: str | None = None,
    revision: int | None = None,
) -> dict[str, Any]:
    from . import note_inventory

    if action not in {"track", "rebind"} or not note_changes.DIGEST.fullmatch(expected_sha256):
        raise InvalidInput("invalid note registration request")
    note_changes.validate(
        [{"identity": "path:" + relative, "note_path": relative, "sha256": expected_sha256}]
    )
    if not relative.lower().endswith(".md"):
        raise InvalidInput("note registration requires Markdown")
    if action == "track" and (identity is not None or revision is not None):
        raise InvalidInput("invalid track arguments")
    if action == "rebind" and (identity is None or type(revision) is not int or revision < 1):
        raise InvalidInput("rebind requires identity and revision")
    with database.locked(config):
        main = database.connect(config.state_root)
        try:
            client_id = auth.client(main, authorization)["id"]
            auth.grant(main, client_id, vault_id, "add")
            vault = auth.grant(main, client_id, vault_id, "query")
            root = vaults.checked_vault(config, vault)
            contained(root, relative)
            records = note_identity_store.records(config, vault_id)
            for record in records:
                validate(record)
            if action == "track":
                from . import note_search_inventory

                inventory = note_search_inventory.read(config, main, vault, selected=set())
                note = next((n for n in inventory.notes if n.relative_path == relative), None)
                if note is None:
                    raise InvalidInput("track requires a current visible note")
                existing = next((r for r in records if r.file_key == note.file_key), None)
                identity = (
                    "publication:" + note.publication_id
                    if note.publication_id
                    else note.identity or "note:" + str(uuid4())
                )
                if existing is not None:
                    identity = existing.identity
            else:
                existing = next((r for r in records if r.identity == identity), None)
                if existing is None or existing.revision != revision:
                    raise IntegrityError("note identity revision conflict")
                raw = note_inventory.read(config, main, vault, registered=False)
                # A still-visible original cannot be silently replaced by a same-content copy.
                if any(n.file_key == existing.file_key for n in raw.notes):
                    raise IntegrityError("original tracked file is still visible")
                if existing.publication_id is not None:
                    parent = raw.locations.get(existing.publication_id)
                    if parent is not None and original_in_bundle(root, parent, existing.file_key):
                        raise IntegrityError("original tracked publication still exists")
                    old = contained(root, existing.relative_path)
                    if (
                        old.exists()
                        and note_index.read_note(
                            root, existing.relative_path, note_index.Limits().note_bytes
                        ).file_key
                        == existing.file_key
                    ):
                        raise IntegrityError("original tracked publication still exists")
                note = candidate(config, main, vault, relative, existing.publication_id)
            if note.sha256 != expected_sha256 or note.file_key is None:
                raise IntegrityError("registration hash conflict or file birth unavailable")
            info = root.stat()
            key = (info.st_dev, info.st_ino)
            if existing is not None and existing.vault_key != key:
                raise IntegrityError("note identity vault changed")
            if any(r.file_key == note.file_key and r.identity != identity for r in records):
                raise IntegrityError("replacement file already has another identity")
            current = note_index.read_note(root, relative, note_index.Limits().note_bytes)
            if (current.sha256, current.file_key) != (note.sha256, note.file_key):
                raise IntegrityError("note changed before registration commit")
            auth.grant(main, client_id, vault_id, "add")
            checked = vaults.checked_vault(
                config, auth.grant(main, client_id, vault_id, "query")
            ).stat()
            if (checked.st_dev, checked.st_ino) != key:
                raise IntegrityError("note identity vault changed")
            if action == "track" and existing is not None:
                return {
                    "note_identity": existing.identity,
                    "revision": existing.revision,
                    "note_path": relative,
                    "note_write_performed": False,
                }
            assert identity is not None
            value = Registration(
                identity,
                note.publication_id,
                relative,
                note.sha256,
                note.file_key,
                key,
                note_identity_store.next_revision(config, vault_id, identity)
                if existing is None
                else existing.revision + 1,
            )
            note_identity_store.save(
                config,
                vault_id,
                client_id,
                value,
                expected_revision=None if existing is None else existing.revision,
            )
            return {
                "note_identity": identity,
                "revision": value.revision,
                "note_path": relative,
                "note_write_performed": False,
            }
        finally:
            main.close()
