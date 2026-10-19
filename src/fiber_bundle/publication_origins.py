"""Immutable creation evidence. Older or unproven publications remain protected on revoke."""

import hashlib
import json
import math
from typing import Any

from . import note_changes, note_index
from .article_capture import read_bounded, write_new
from .config import Config, IntegrityError, contained, make_directory
from .inputs import canonical


def save(
    config: Config, publication: str, run: str, vault: str, note: note_index.Note, manifest: str
) -> None:
    for value in (publication, run, vault):
        note_changes.identifier(value)
    if note.file_key is None:
        return  # Platforms without a stable physical identity cannot prove revocation ownership.
    record = {
        "schema": 1,
        "publication_id": publication,
        "run_id": run,
        "vault_id": vault,
        "file_key": list(note.file_key),
        "sha256": note.sha256,
        "manifest_sha256": hashlib.sha256(manifest.encode()).hexdigest(),
    }
    root = contained(config.state_root, "publication-origins")
    make_directory(root)
    path = contained(root, publication + ".json")
    if path.exists():
        if load(config, publication) != record:
            raise IntegrityError("publication creation evidence changed")
    else:
        write_new(root, path.name, canonical(record).encode())


def load(config: Config, publication: str) -> dict[str, Any] | None:
    note_changes.identifier(publication)
    root = contained(config.state_root, "publication-origins")
    path = contained(root, publication + ".json")
    if not path.exists():
        return None
    try:
        raw = read_bounded(root, path.name, 4096)
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or set(value)
            != {
                "schema",
                "publication_id",
                "run_id",
                "vault_id",
                "file_key",
                "sha256",
                "manifest_sha256",
            }
            or type(value["schema"]) is not int
            or value["schema"] != 1
            or value["publication_id"] != publication
            or canonical(value).encode() != raw
        ):
            raise ValueError("invalid origin")
        for field in ("publication_id", "run_id", "vault_id"):
            note_changes.identifier(value[field])
        for field in ("sha256", "manifest_sha256"):
            if not isinstance(value[field], str) or not note_changes.DIGEST.fullmatch(value[field]):
                raise ValueError("invalid origin digest")
        key = value["file_key"]
        if (
            not isinstance(key, list)
            or len(key) != 3
            or any(type(v) is not int or v < 0 for v in key[:2])
            or not isinstance(key[2], str)
        ):
            raise ValueError("invalid origin physical key")
        birth = float.fromhex(key[2])
        if not math.isfinite(birth) or birth.hex() != key[2]:
            raise ValueError("invalid origin birth")
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        raise IntegrityError("invalid publication creation evidence") from exc
    return value
