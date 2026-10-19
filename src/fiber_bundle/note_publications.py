"""Read-only binding of relocated article bundles to their existing publication ledger."""

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .article_capture import read_bounded
from .articles import ArticleError
from .config import IntegrityError, contained
from .inputs import canonical

MARKER = ".fiber-publication.json"


@dataclass(frozen=True)
class Binding:
    publication_id: str
    original_sha256: str
    marker_sha256: str


def binding(row: sqlite3.Row) -> Binding:
    try:
        files = json.loads(row["manifest_json"])
        original = files["note.md"]["sha256"]
        if not isinstance(original, str) or not re.fullmatch(r"[0-9a-f]{64}", original):
            raise ValueError("invalid note digest")
    except (ValueError, KeyError, TypeError) as exc:
        raise IntegrityError("publication provenance invalid") from exc
    marker = canonical(
        {"publication_id": row["id"], "version_id": row["version_id"], "files": files}
    ).encode()
    return Binding(row["id"], original, hashlib.sha256(marker).hexdigest())


def read_marker(vault: Path, relative: str, limit: int) -> bytes:
    path = contained(vault, relative + "/" + MARKER)
    try:
        return read_bounded(path.parent, path.name, limit)
    except (OSError, ArticleError) as exc:
        raise IntegrityError("publication marker unavailable or invalid") from exc


def identify(data: bytes, bindings: Mapping[str, Binding]) -> Binding | None:
    """An unregistered marker never establishes provenance or exposes a partial bundle."""
    try:
        marker = json.loads(data)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    if not isinstance(marker, dict) or not isinstance(marker.get("publication_id"), str):
        return None
    result = bindings.get(marker["publication_id"])
    if result is not None and hashlib.sha256(data).hexdigest() != result.marker_sha256:
        raise IntegrityError("publication marker differs from ledger")
    return result
