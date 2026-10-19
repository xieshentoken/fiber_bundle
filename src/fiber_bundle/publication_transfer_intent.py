"""Freeze a published bundle's location and provenance without moving its original archive."""

import hashlib
import json
import math
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import (
    file_exchange,
    note_changes,
    note_identity_store,
    note_inventory,
    note_migration,
    note_publications,
    note_transfer_intent,
    publication_files,
)
from . import (
    note_migration_plans as plans,
)
from . import (
    note_transfer_links as links,
)
from . import (
    note_transfer_mocs as mocs,
)
from .article_capture import read_bounded
from .config import Config, IntegrityError, InvalidInput, contained, make_directory

PLAN_FIELDS = plans.FIELDS | {"publication", "bundle_files", "original_vault_id"}
ROW_FIELDS = {
    "id",
    "vault_id",
    "source_url",
    "content_digest",
    "vault_dev",
    "vault_ino",
    "version_id",
    "relative_path",
    "manifest_json",
    "state",
    "staging_path",
    "error_code",
}


def validate_plan(value: Any, client: str, source: str, target: str) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != PLAN_FIELDS
        or type(value["schema"]) is not int
        or value["schema"] != 2
    ):
        raise IntegrityError("invalid publication transfer scope")
    identity = value["note_identity"]
    if not isinstance(identity, str) or not identity.startswith("publication:"):
        raise IntegrityError("publication transfer requires stable publication identity")
    note_changes.identifier(identity.split(":", 1)[1])
    base = {k: v for k, v in value.items() if k in plans.FIELDS}
    base.update(schema=1, note_identity="note:" + identity.split(":", 1)[1])
    plans.validate(base, client, source, target)
    row = value["publication"]
    if (
        not isinstance(row, dict)
        or set(row) != ROW_FIELDS
        or row["id"] != identity.split(":", 1)[1]
        or row["vault_id"] != source
        or row["state"] != "published"
        or [row["vault_dev"], row["vault_ino"]] != value["source_vault_key"]
    ):
        raise IntegrityError("invalid publication provenance snapshot")
    for name in ("id", "version_id"):
        note_changes.identifier(row[name])
    note_changes.identifier(value["original_vault_id"])
    if "moc_document_needs_review" in value["review_flags"]:
        raise IntegrityError("published MOC documents require separate migration review")
    files = value["bundle_files"]
    if not isinstance(files, list) or not 2 <= len(files) <= 130:
        raise IntegrityError("invalid publication file scope")
    names = []
    for item in files:
        if not isinstance(item, dict) or set(item) != {
            "source_path",
            "target_name",
            "sha256",
            "bytes",
            "file_key",
        }:
            raise IntegrityError("invalid publication transfer file")
        path = item["source_path"]
        if path.endswith("/" + note_publications.MARKER):
            note_transfer_intent.local_path(path.rsplit("/", 1)[0])
        else:
            note_transfer_intent.local_path(path)
        name = item["target_name"]
        if (
            not isinstance(name, str)
            or len(name) > 1024
            or "\\" in name
            or any(p in {"", ".", ".."} for p in name.split("/"))
            or any(p.startswith(".") for p in name.split("/"))
            and name != note_publications.MARKER
        ):
            raise IntegrityError("invalid publication destination filename")
        if (
            len(name.split("/")) > 2
            or type(item["bytes"]) is not int
            or not 0 < item["bytes"] <= links.MAX_ATTACHMENT_BYTES
        ):
            raise IntegrityError("publication transfer file limit exceeded")
        if not isinstance(item["sha256"], str) or not note_changes.DIGEST.fullmatch(item["sha256"]):
            raise IntegrityError("invalid publication transfer digest")
        key = item["file_key"]
        if (
            not isinstance(key, list)
            or len(key) != 3
            or any(type(v) is not int or v < 0 for v in key[:2])
        ):
            raise IntegrityError("invalid publication physical identity")
        birth = float.fromhex(key[2])
        if not math.isfinite(birth) or birth.hex() != key[2]:
            raise IntegrityError("invalid publication birth")
        names.append(name)
    if names != sorted(set(names)) or note_publications.MARKER not in names:
        raise IntegrityError("publication transfer filenames incomplete")
    if sum(f["bytes"] for f in files) > links.MAX_TOTAL_BYTES:
        raise IntegrityError("publication transfer byte limit exceeded")


def observe(
    config: Config,
    db: Any,
    client: str,
    source: str,
    target: str,
    identity: str,
    revision: int,
    source_path: str,
    target_path: str,
    expected: str,
) -> tuple[dict[str, Any], dict[str, bytes], list[str]]:
    for path in (source_path, target_path):
        plans.note_path(path)
    if not identity.startswith("publication:") or type(revision) is not int or revision < 1:
        raise IntegrityError("publication migration requires current stable registration")
    src_row, dst_row = note_migration.endpoints(config, db, client, source, target)
    from .publication_locations import get as layout_location

    if layout_location(db, "article", identity.removeprefix("publication:")):
        raise InvalidInput("obsidian layout cross-Vault migration requires a dedicated plan")
    src, dst = note_inventory.read(config, db, src_row), note_inventory.read(config, db, dst_row)
    note = next(
        (n for n in src.notes if n.identity == identity and n.relative_path == source_path), None
    )
    before = next(
        (r for r in note_identity_store.records(config, source) if r.identity == identity), None
    )
    if (
        note is None
        or before is None
        or before.revision != revision
        or note.sha256 != expected
        or note.file_key != before.file_key
        or before.publication_id is None
    ):
        raise IntegrityError("publication migration source evidence changed")
    row = src.publications.get(before.publication_id)
    if row is None or note.publication_path is None:
        raise IntegrityError("publication migration provenance absent")
    if any(r.identity == identity for r in note_identity_store.records(config, target)):
        raise IntegrityError("publication target already owns identity")
    if db.execute(
        "SELECT 1 FROM article_publications WHERE vault_id=? AND source_url=? AND content_digest=?",
        (target, row["source_url"], row["content_digest"]),
    ).fetchone():
        raise IntegrityError("publication target already has this content; refuse identity merge")
    target_bundle = target_path.rsplit("/", 1)[0] if "/" in target_path else ""
    if not target_bundle:
        raise IntegrityError("publication migration requires a new destination bundle")
    fd = file_exchange.parent_fd(dst.root, target_bundle)
    try:
        info = os.fstat(fd)
        try:
            os.stat(target_bundle.rsplit("/", 1)[-1], dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise IntegrityError("publication destination bundle must be absent")
    finally:
        os.close(fd)
    for index in range(1, len(target_bundle.split("/"))):
        marker = contained(
            dst.root, "/".join(target_bundle.split("/")[:index]) + "/" + note_publications.MARKER
        )
        if marker.exists():
            raise IntegrityError("publication transfer cannot nest inside another publication")
    if len(row["manifest_json"].encode()) > 65536:
        raise IntegrityError("publication manifest exceeds transfer limit")
    manifest = json.loads(row["manifest_json"])
    if not isinstance(manifest, dict) or not 1 <= len(manifest) <= 129 or "note.md" not in manifest:
        raise IntegrityError("publication manifest scope invalid")
    for name, descriptor in manifest.items():
        note_transfer_intent.local_path(name)
        if (
            len(name.split("/")) > 2
            or not isinstance(descriptor, dict)
            or set(descriptor) != {"sha256", "bytes"}
            or type(descriptor["bytes"]) is not int
            or not 0 < descriptor["bytes"] <= links.MAX_ATTACHMENT_BYTES
            or not isinstance(descriptor["sha256"], str)
            or not note_changes.DIGEST.fullmatch(descriptor["sha256"])
        ):
            raise IntegrityError("publication manifest file invalid")
    files, evidence = {}, []
    names = {name for name in manifest if name != "note.md"}
    # Preserve the note's exact bytes and relative bundle layout. Human added external media
    # is refused rather than silently producing broken copied links or changing provenance.
    note_file = contained(src.root, source_path)
    data = read_bounded(note_file.parent, note_file.name, 1024 * 1024)
    _, _, dependencies, unresolved = links.prepare(
        src.root, source_path, target_path, identity, data
    )
    prefix = note.publication_path + "/"
    for dependency in dependencies:
        path = dependency["source_path"]
        if not path.startswith(prefix):
            raise IntegrityError("published note has an attachment outside its bundle")
        names.add(path[len(prefix) :])
    mapping = {name: prefix + name for name in names}
    mapping[target_path.rsplit("/", 1)[-1]] = source_path
    mapping[note_publications.MARKER] = prefix + note_publications.MARKER
    if len(mapping) > 130:
        raise IntegrityError("publication transfer attachment limit exceeded")
    total = 0
    for name, relative in sorted(mapping.items()):
        payload, key = links.read(src.root, relative)
        total += len(payload)
        if total > links.MAX_TOTAL_BYTES:
            raise IntegrityError("publication transfer byte limit exceeded")
        descriptor = manifest.get(name) if relative != source_path else None
        if descriptor is not None and (
            len(payload) != descriptor["bytes"]
            or hashlib.sha256(payload).hexdigest() != descriptor["sha256"]
        ):
            raise IntegrityError("publication original attachment was human modified")
        files[name] = payload
        evidence.append(
            {
                "source_path": relative,
                "target_name": name,
                "file_key": key,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    original = db.execute(
        "SELECT s.vault_id FROM source_versions v JOIN sources s ON s.id=v.source_id WHERE v.id=?",
        (row["version_id"],),
    ).fetchone()
    if original is None:
        raise IntegrityError("publication original archive binding missing")
    source_mocs, target_mocs = (
        note_migration.associations(config, source, src),
        note_migration.associations(config, target, dst),
    )
    flags = []
    if note_migration.LINKS.search(note.text):
        flags.append("source_links_need_review")
    if any(n is not note and note_migration.LINKS.search(n.text) for n in src.notes):
        flags.append("other_source_links_need_review")
    if any(note_migration.LINKS.search(n.text) for n in dst.notes):
        flags.append("target_links_need_review")
    for label, records in [("source", source_mocs), ("target", target_mocs)]:
        if any(identity in r["members"] for r in records.values()):
            flags.append(label + "_moc_membership")
    if "<!-- fiber-moc:" in note.text:
        flags.append("moc_document_needs_review")
    a, b = src.root.stat(), dst.root.stat()
    if a.st_dev != b.st_dev:
        flags.append("cross_filesystem_transfer")
    plan = {
        "schema": 2,
        "client_id": client,
        "source_vault_id": source,
        "target_vault_id": target,
        "note_identity": identity,
        "revision": revision,
        "source_path": source_path,
        "target_path": target_path,
        "source_sha256": expected,
        "source_file_key": list(before.file_key),
        "source_vault_key": [a.st_dev, a.st_ino],
        "target_vault_key": [b.st_dev, b.st_ino],
        "target_parent_key": [info.st_dev, info.st_ino],
        "registration_sha256": plans.digest(asdict(before)),
        "source_inventory_sha256": note_migration.inventory_digest(src),
        "target_inventory_sha256": note_migration.inventory_digest(dst),
        "source_mocs_sha256": plans.digest(source_mocs),
        "target_mocs_sha256": plans.digest(target_mocs),
        "review_flags": sorted(flags),
        "publication": dict(row),
        "bundle_files": evidence,
        "original_vault_id": original[0],
    }
    validate_plan(plan, client, source, target)
    return plan, files, unresolved


def directory(config: Config, execution: str) -> Path:
    if not note_changes.DIGEST.fullmatch(execution):
        raise IntegrityError("invalid publication transfer execution ID")
    return contained(config.state_root, "publication-transfer-files/" + execution)


def save_files(config: Config, execution: str, files: dict[str, bytes]) -> None:
    root = directory(config, execution)
    make_directory(root.parent)
    if root.exists():
        publication_files.verify(root.parent, root.name, files)
    else:
        publication_files.stage(root.parent, root.name, files)


def files(config: Config, execution: str, value: dict[str, Any]) -> dict[str, bytes]:
    root = directory(config, execution)
    result = {}
    for item in value["plan"]["bundle_files"]:
        path = contained(root, item["target_name"])
        data = read_bounded(path.parent, path.name, item["bytes"])
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise IntegrityError("publication transfer artifact changed")
        result[item["target_name"]] = data
    publication_files.verify(root.parent, root.name, result)
    return result


def validate(value: dict[str, Any]) -> None:
    plan = value["plan"]
    validate_plan(plan, plan["client_id"], plan["source_vault_id"], plan["target_vault_id"])
    transfer = value["transfer"]
    fields = {"moc_changes", "unresolved_note_links"}
    if not isinstance(transfer, dict) or set(transfer) not in (
        fields,
        fields | {"incoming_note_links"},
    ):
        raise IntegrityError("invalid publication transfer dependencies")
    note_transfer_intent.validate_inbound(transfer.get("incoming_note_links", []))
    unresolved = transfer["unresolved_note_links"]
    if (
        not isinstance(unresolved, list)
        or len(unresolved) > 10000
        or unresolved != sorted(set(unresolved))
    ):
        raise IntegrityError("invalid publication note-link scope")
    for path in unresolved:
        note_transfer_intent.local_path(path)
    mocs.validate(transfer["moc_changes"], plan)
    if value["request_id"] != value["plan_id"]:
        note_changes.identifier(value["request_id"])
