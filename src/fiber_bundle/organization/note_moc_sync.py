"""Journaled moc-only note projection, with ownership and stable identity continuity."""

import json
import os
import sqlite3
from dataclasses import asdict, replace
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from .. import (
    file_exchange,
    moc_file_journal,
    moc_registration,
    note_identities,
    note_identity_store,
    note_placement,
)
from ..config import Config, IntegrityError, contained
from ..objects import digest
from ..publication_file_io import read
from . import note_moc_fields as fields
from . import packages, store


def key(vault: str, identity: str) -> str:
    return vault + ":" + identity


def track(config: Config, db: sqlite3.Connection, client: str, vault: str, entity: Any) -> None:
    if entity["kind"] != "note":
        return
    if not entity.get("file_key"):
        raise IntegrityError("organization_note_identity_unavailable")
    records = note_identity_store.records(config, vault)
    if any(r.identity == entity["id"] for r in records):
        return
    root, _ = packages.context(config, db, client, vault, write=True)
    info = root.stat()
    revision = note_identity_store.next_revision(config, vault, entity["id"])
    record = note_identity_store.Registration(
        entity["id"],
        entity.get("publication_id"),
        entity["path"],
        entity["sha256"],
        tuple(entity["file_key"]),
        (info.st_dev, info.st_ino),
        revision,
    )
    note_identities.validate(record)
    note_identity_store.save(config, vault, client, record, expected_revision=None)


def check(config: Config, db: sqlite3.Connection, client: str, vault: str, entity: Any) -> None:
    if entity["kind"] != "note":
        return
    root, allowed = packages.context(config, db, client, vault, write=True)
    # An unregistered imported Draft is still a package, never an ordinary note.
    for parent in PurePosixPath(entity["path"]).parents:
        if str(parent) == ".":
            continue
        manifest = str(parent / "manifest.json")
        if not allowed.allows(manifest):
            continue
        try:
            value = json.loads(packages.scoped_read(root, allowed, manifest))
        except (FileNotFoundError, ValueError):
            continue
        if isinstance(value, dict) and str(value.get("schema_version", "")).startswith(
            "tkc.expert-skill/"
        ):
            raise IntegrityError("note_moc_expert_package_untouched")
    raw = packages.scoped_read(root, allowed, entity["path"])
    record = store.get(config, "note_moc", key(vault, entity["id"]))
    accepted = {entity["sha256"]}
    if (
        record
        and record["path"] == entity["path"]
        and entity["sha256"]
        in {record["after_sha256"], record["source"]["sha256"], record.get("before_sha256")}
    ):
        accepted.add(record["after_sha256"])
    if digest(raw) not in accepted:
        raise IntegrityError("note_moc_source_changed")
    fields.field(raw.decode("utf-8"))


def accepts(config: Config, vault: str, entity: str, before: str, after: str) -> bool:
    record = store.get(config, "note_moc", key(vault, entity))
    return bool(
        record
        and record["after_sha256"] == after
        and before
        in {record["source"]["sha256"], record.get("before_sha256"), record["after_sha256"]}
    )


def decorate(entity: Any, record: Any) -> None:
    if not record:
        return
    entity["_moc_owned"] = record["owned"]
    entity["_manual_text"] = fields.human_text(entity["text"], record["owned"])
    if record["path"] == entity["path"] and record["after_sha256"] == entity["sha256"]:
        entity["_classification_source"] = record["source"]


def desired(config: Config, vault: str, identity: str) -> tuple[list[str], dict[str, str]]:
    paths = {mid: path for path, mid in moc_registration.bindings(config, vault).items()}
    records = note_placement.records(config, vault)
    links = [
        "[[" + paths[mid].removesuffix(".md") + "]]"
        for mid, record in records.items()
        if record and identity in record.members and mid in paths
    ]
    stems: dict[str, list[str]] = {}
    for path in paths.values():
        stems.setdefault(PurePosixPath(path).stem, []).append(path.removesuffix(".md"))
    aliases = {stem: values[0] for stem, values in stems.items() if len(values) == 1}
    return sorted(links), aliases


def _registration(value: Any) -> note_identity_store.Registration:
    return note_identity_store.Registration(
        value["identity"],
        value["publication_id"],
        value["relative_path"],
        value["sha256"],
        tuple(value["file_key"]),
        tuple(value["vault_key"]),
        value["revision"],
    )


def recover(config: Config, db: sqlite3.Connection, client: str, vault: str, record: Any) -> None:
    pending = record.get("pending")
    if not pending:
        if not record.get("location_synced"):
            _location(config, db, client, vault, record)
            record["location_synced"] = True
            store.put(config, "note_moc", key(vault, record["entity"]), vault, record)
        return
    root, allowed = packages.context(config, db, client, vault, write=True)
    if not allowed.allows(record["path"]):
        raise IntegrityError("note_moc_scope_changed")
    home = contained(config.state_root, "moc-note-files/" + pending["operation"])
    raw, physical = read(root, record["path"])
    saved, saved_key = read(home, "candidate.md")
    before, after = pending["before"], pending["after"]
    if digest(raw) == before["sha256"] and physical == list(before["file_key"]):
        if digest(saved) != record["after_sha256"] or saved_key != list(after["file_key"]):
            raise IntegrityError("note_moc_candidate_changed")
        src = file_exchange.parent_fd(home, "candidate.md")
        dst = file_exchange.parent_fd(root, record["path"])
        try:
            file_exchange.rename(src, "candidate.md", dst, Path(record["path"]).name, exchange=True)
        finally:
            os.close(src)
            os.close(dst)
        raw, physical = read(root, record["path"])
        saved, saved_key = read(home, "candidate.md")
    if (
        digest(raw) != record["after_sha256"]
        or physical != list(after["file_key"])
        or digest(saved) != before["sha256"]
        or saved_key != list(before["file_key"])
    ):
        # A user edit/replacement between observation and exchange belongs at
        # the original path. Swap it back only while our exact candidate is there.
        if digest(raw) == record["after_sha256"] and physical == list(after["file_key"]):
            src = file_exchange.parent_fd(home, "candidate.md")
            dst = file_exchange.parent_fd(root, record["path"])
            try:
                file_exchange.rename(
                    src, "candidate.md", dst, Path(record["path"]).name, exchange=True
                )
            finally:
                os.close(src)
                os.close(dst)
        raise IntegrityError("note_moc_exchange_conflict_preserved")
    current = next(
        (r for r in note_identity_store.records(config, vault) if r.identity == record["entity"]),
        None,
    )
    old, new = _registration(pending["registration_before"]), _registration(after)
    if current == old:
        note_identity_store.save(config, vault, client, new, expected_revision=old.revision)
    elif current != new:
        raise IntegrityError("note_moc_identity_conflict")
    _location(config, db, client, vault, record)
    record["location_synced"] = True
    record.pop("pending")
    store.put(config, "note_moc", key(vault, record["entity"]), vault, record)


def _location(config: Config, db: sqlite3.Connection, client: str, vault: str, record: Any) -> None:
    from . import note_moc_locations

    root, allowed = packages.context(config, db, client, vault, write=True)
    if not allowed.allows(record["path"]):
        raise IntegrityError("note_moc_scope_changed")
    raw, physical = read(root, record["path"])
    if digest(raw) != record["after_sha256"] or physical != record["after_file_key"]:
        raise IntegrityError("note_moc_managed_source_changed")
    wanted, aliases = desired(config, vault, record["entity"])
    note_moc_locations.apply(db, vault, record, physical, wanted, aliases)


def recover_all(config: Config, db: sqlite3.Connection, client: str, vault: str) -> None:
    for record in store.rows(config, "note_moc", vault):
        recover(config, db, client, vault, record)


def sync(config: Config, db: sqlite3.Connection, client: str, vault: str, entity: Any) -> bool:
    if entity["kind"] != "note":
        return False
    identity = key(vault, entity["id"])
    previous = store.get(config, "note_moc", identity)
    if previous and previous.get("pending"):
        recover(config, db, client, vault, previous)
        previous = store.get(config, "note_moc", identity)
    check(config, db, client, vault, entity)
    root, allowed = packages.context(config, db, client, vault, write=True)
    raw, physical = read(root, entity["path"])
    wanted, aliases = desired(config, vault, entity["id"])
    if any(not allowed.allows(fields.target(v) + ".md") for v in wanted):
        raise IntegrityError("note_moc_target_denied")
    candidate, owned = fields.render(
        raw,
        wanted,
        previous["owned"] if previous else [],
        lambda value: aliases.get(fields.target(value), fields.target(value)),
    )
    if candidate == raw:
        return False
    registration = next(
        (r for r in note_identity_store.records(config, vault) if r.identity == entity["id"]), None
    )
    if registration is None or list(registration.file_key) != physical:
        raise IntegrityError("note_moc_note_not_tracked")
    operation = str(uuid4())
    home = contained(config.state_root, "moc-note-files/" + operation)
    home.mkdir(parents=True, mode=0o700)
    moc_file_journal.write(home / "candidate.md", candidate)
    os.chmod(home / "candidate.md", (root / entity["path"]).stat().st_mode & 0o777)
    _, after_key = read(home, "candidate.md")
    before = replace(registration, relative_path=entity["path"], sha256=digest(raw))
    after = replace(
        before,
        sha256=digest(candidate),
        file_key=tuple(after_key),
        revision=registration.revision + 1,
    )
    # Preserve the pre-projection classifier input so metadata alone never bills a new analysis.
    source = {
        "text": raw.decode("utf-8-sig"),
        "sha256": digest(raw),
        "version": digest(raw),
        "file_key": physical,
    }
    versions = []
    source_identity = entity.get("_inventory_identity", entity["id"])
    if previous and previous["after_sha256"] == digest(raw) and previous["path"] == entity["path"]:
        source = previous["source"]
        source_identity = previous["source_identity"]
        versions = previous["versions"].copy()
    versions.extend(
        [
            {"sha256": digest(raw), "file_key": physical},
            {"sha256": digest(candidate), "file_key": after_key},
        ]
    )
    value = {
        "entity": entity["id"],
        "path": entity["path"],
        "owned": owned,
        "prior_owned": previous["owned"] if previous else [],
        "after_sha256": digest(candidate),
        "after_file_key": after_key,
        "before_sha256": digest(raw),
        "versions": versions,
        "source": source,
        "source_identity": source_identity,
        "pending": {"operation": operation, "before": asdict(before), "after": asdict(after)},
    }
    # The actual prior registration can have an older path/hash after a user edit or move.
    value["pending"]["registration_before"] = asdict(registration)
    store.put(config, "note_moc", identity, vault, value)
    recover(config, db, client, vault, value)
    return True
