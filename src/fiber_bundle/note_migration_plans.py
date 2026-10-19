"""Immutable bounded cross-Vault preflight metadata; never a file-write authorization."""

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, cast

from . import note_changes
from .answer_model_output import reject_constant, unique_object
from .article_capture import read_bounded
from .articles import ArticleError
from .config import Config, IntegrityError, contained, make_directory, sync_directory
from .inputs import canonical, text

MAX_PLANS = 1000
MAX_BYTES = 16384
FLAGS = {
    "source_links_need_review",
    "other_source_links_need_review",
    "target_links_need_review",
    "source_moc_membership",
    "target_moc_membership",
    "moc_document_needs_review",
    "cross_filesystem_transfer",
}
FIELDS = {
    "schema",
    "client_id",
    "source_vault_id",
    "target_vault_id",
    "note_identity",
    "revision",
    "source_path",
    "target_path",
    "source_sha256",
    "source_file_key",
    "source_vault_key",
    "target_vault_key",
    "target_parent_key",
    "registration_sha256",
    "source_inventory_sha256",
    "target_inventory_sha256",
    "source_mocs_sha256",
    "target_mocs_sha256",
    "review_flags",
}


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def note_path(value: Any) -> None:
    text(value, limit=1024)
    note_changes.validate([{"identity": "path:" + value, "note_path": value, "sha256": "0" * 64}])
    if not value.lower().endswith(".md") or len(value.split("/")) > 33:
        raise IntegrityError("migration requires a visible Markdown path")


def validate(value: Any, client: str, source: str, target: str) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != FIELDS
        or type(value["schema"]) is not int
        or value["schema"] != 1
    ):
        raise IntegrityError("invalid migration preflight fields")
    for key in ["client_id", "source_vault_id", "target_vault_id"]:
        note_changes.identifier(value[key])
    if (value["client_id"], value["source_vault_id"], value["target_vault_id"]) != (
        client,
        source,
        target,
    ) or source == target:
        raise IntegrityError("migration preflight scope mismatch")
    identity = value["note_identity"]
    if not isinstance(identity, str) or not identity.startswith("note:"):
        raise IntegrityError("migration preflight requires a historical stable identity")
    note_changes.identifier(identity[5:])
    if type(value["revision"]) is not int or value["revision"] < 1:
        raise IntegrityError("invalid migration identity revision")
    for name in ["source_path", "target_path"]:
        note_path(value[name])
    for name in [key for key in FIELDS if key.endswith("sha256")]:
        if not isinstance(value[name], str) or not note_changes.DIGEST.fullmatch(value[name]):
            raise IntegrityError("invalid migration metadata digest")
    for name in ["source_vault_key", "target_vault_key", "target_parent_key", "source_file_key"]:
        key = value[name]
        if (
            not isinstance(key, list)
            or len(key) != (3 if name == "source_file_key" else 2)
            or any(type(v) is not int or v < 0 for v in key[:2])
        ):
            raise IntegrityError("invalid migration filesystem identity")
    birth = value["source_file_key"][2]
    try:
        decoded = float.fromhex(birth)
        if not math.isfinite(decoded) or decoded.hex() != birth:
            raise ValueError("invalid birth")
    except (ValueError, TypeError) as exc:
        raise IntegrityError("invalid migration file birth") from exc
    flags = value["review_flags"]
    if (
        not isinstance(flags, list)
        or any(not isinstance(v, str) or v not in FLAGS for v in flags)
        or flags != sorted(set(flags))
    ):
        raise IntegrityError("invalid migration review flags")


def directory(config: Config, client: str, source: str, target: str) -> Path:
    for value in (client, source, target):
        note_changes.identifier(value)
    return contained(config.state_root, f"note-migrations/{client}/{source}/{target}")


def path_for(config: Config, client: str, source: str, target: str, identity: str) -> Path:
    if not isinstance(identity, str) or not note_changes.DIGEST.fullmatch(identity):
        raise IntegrityError("invalid migration plan digest")
    return contained(directory(config, client, source, target), identity + ".json")


def load(config: Config, client: str, source: str, target: str, identity: str) -> dict[str, Any]:
    path = path_for(config, client, source, target, identity)
    try:
        raw = read_bounded(path.parent, path.name, MAX_BYTES)
        if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != identity:
            raise IntegrityError("migration plan digest mismatch")
        value = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
        validate(value, client, source, target)
        if canonical(value).encode() != raw:
            raise IntegrityError("noncanonical migration preflight")
    except (OSError, ArticleError, ValueError, TypeError, RecursionError) as exc:
        raise IntegrityError("migration plan unavailable or invalid") from exc
    return cast(dict[str, Any], value)


def save(config: Config, value: dict[str, Any]) -> str:
    client, source, target = (value[k] for k in ["client_id", "source_vault_id", "target_vault_id"])
    validate(value, client, source, target)
    raw = canonical(value).encode()
    if len(raw) > MAX_BYTES:
        raise IntegrityError("migration plan metadata limit exceeded")
    identity = hashlib.sha256(raw).hexdigest()
    root = directory(config, client, source, target)
    path = path_for(config, client, source, target, identity)
    if path.exists():
        if load(config, client, source, target, identity) != value:
            raise IntegrityError("existing migration plan changed")
        return identity
    if root.exists():
        with os.scandir(root) as items:
            count = 0
            for entry in items:
                count += 1
                if (
                    count >= MAX_PLANS
                    or not entry.name.endswith(".json")
                    or not note_changes.DIGEST.fullmatch(entry.name[:-5])
                    or not entry.is_file(follow_symlinks=False)
                ):
                    raise IntegrityError("migration plan limit or unexpected entry")
    make_directory(root)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(root)
    return identity
