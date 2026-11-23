"""Explicit, operation-bound proof for one added non-Markdown namespace entry.

This proof never updates the original journal or authorizes a file write. Every
original committed-content, directory, scope and metadata check still applies.
"""

import html
import json
import posixpath
import re
import sqlite3
import stat
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from . import cloud_files, note_index, publication_file_io
from .config import Config, IntegrityError, contained
from .inputs import canonical
from .objects import digest

SCHEMA = "committed-namespace-addition/v1"
MAX_ADDITION = 1024 * 1024


def invalid() -> IntegrityError:
    return IntegrityError("placement_namespace_addition_conflict")


def validate_shape(proof: Any) -> None:
    if (
        not isinstance(proof, dict)
        or set(proof)
        != {
            "schema",
            "operation_id",
            "payload_sha256",
            "root_key",
            "original_namespace_sha256",
            "current_namespace_sha256",
            "addition",
        }
        or proof.get("schema") != SCHEMA
    ):
        raise invalid()
    if not isinstance(proof["operation_id"], str) or not 1 <= len(proof["operation_id"]) <= 128:
        raise invalid()
    if any(
        not isinstance(proof[key], str) or not re.fullmatch(r"[a-f0-9]{64}", proof[key])
        for key in ("payload_sha256", "original_namespace_sha256", "current_namespace_sha256")
    ):
        raise invalid()
    if (
        not isinstance(proof["root_key"], list)
        or len(proof["root_key"]) != 2
        or any(type(part) is not int or part < 0 for part in proof["root_key"])
    ):
        raise invalid()
    addition = proof["addition"]
    if not isinstance(addition, dict) or set(addition) != {"path", "sha256", "file_key", "size"}:
        raise invalid()
    path = addition["path"]
    if not isinstance(path, str) or not path or len(path) > 1000 or path.lower().endswith(".md"):
        raise invalid()
    if (
        type(addition["size"]) is not int
        or not 0 < addition["size"] <= MAX_ADDITION
        or not isinstance(addition["sha256"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", addition["sha256"])
        or not isinstance(addition["file_key"], list)
        or len(addition["file_key"]) != 3
    ):
        raise invalid()
    if (
        any(type(part) is not int or part < 0 for part in addition["file_key"][:2])
        or not isinstance(addition["file_key"][2], str)
        or len(addition["file_key"][2]) > 80
    ):
        raise invalid()


def bind(
    config: Config, db: sqlite3.Connection, value: Any, root: Path, scope: Any, proof: Any
) -> None:
    validate_shape(proof)
    row = db.execute(
        "SELECT kind,state,payload,vault_id FROM vault_operations WHERE id=?",
        (proof["operation_id"],),
    ).fetchone()
    if (
        row is None
        or row["kind"] not in {"note_placement", "maintenance_archive"}
        or row["state"] not in {"applying", "reconcile", "completed"}
        or row["vault_id"] != value.get("vault_id")
        or not value.get("metadata_committed")
        or value.get("action") not in {"archive", "expert_archive"}
        or digest(row["payload"].encode()) != proof["payload_sha256"]
        or canonical(json.loads(row["payload"])) != canonical(value)
    ):
        raise invalid()
    info = root.stat()
    if proof["root_key"] != [info.st_dev, info.st_ino] or proof["root_key"] != value.get(
        "root_key", value.get("fixed", {}).get("root_key")
    ):
        raise invalid()
    if proof["original_namespace_sha256"] != value.get("link_observation", {}).get("path_sha256"):
        raise invalid()
    file(root, scope, proof)


def file(root: Path, scope: Any, proof: Any) -> None:
    addition = proof["addition"]
    relative = addition["path"]
    if not scope.allows(relative):
        raise invalid()
    path = contained(root, relative)
    cloud_files.require_local(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != addition["size"]:
        raise invalid()
    raw, key = publication_file_io.read(root, relative)
    if (
        len(raw) != addition["size"]
        or digest(raw) != addition["sha256"]
        or key != addition["file_key"]
    ):
        raise invalid()


def normalized(text: str) -> str:
    # Match URL/HTML/NFC spelling variants conservatively, including code and prose.
    # No occurrence is stronger than resolving only currently supported Markdown.
    for _ in range(3):
        decoded = html.unescape(unquote(text))
        if decoded == text:
            break
        text = decoded
    return unicodedata.normalize("NFC", text).replace("\\", "").casefold()


def paths(root: Path, scope: Any, value: Any, proof: Any, observed: list[str]) -> list[str]:
    validate_shape(proof)
    if not value.get("metadata_committed"):
        raise invalid()
    added = proof["addition"]["path"]
    if (
        observed.count(added) != 1
        or digest(canonical(observed).encode()) != proof["current_namespace_sha256"]
    ):
        raise invalid()
    reduced = [path for path in observed if path != added]
    if digest(canonical(reduced).encode()) != proof["original_namespace_sha256"]:
        raise invalid()
    file(root, scope, proof)
    name = normalized(posixpath.basename(added))
    stem = name.rsplit(".", 1)[0]
    if not name or not stem:
        raise invalid()
    total = 0
    original_notes = value["link_observation"]
    note_paths = original_notes.get(
        "note_paths", sorted(original_notes.get("before_notes", original_notes.get("notes", {})))
    )
    if not isinstance(note_paths, list) or not note_paths or len(note_paths) > 20000:
        raise invalid()
    from .note_read_session import current

    session = current.get()
    if session is not None:
        session.counts["namespace_addition_checks"] = (
            session.counts.get("namespace_addition_checks", 0) + 1
        )
    for original in note_paths:
        actual = value["moves"].get(original, original)
        if not scope.allows(actual):
            raise invalid()
        # The existing bounded session reuses only a freshly stat-checked body;
        # read_note still checks cloud status and symlink ancestry on every call.
        note = note_index.read_note(root, actual, note_index.Limits().note_bytes)
        total += len(note.text.encode("utf-8"))
        if total > 256 * 1024 * 1024:
            raise invalid()
        text = normalized(note.text)
        if name in text or stem in text:
            raise IntegrityError("placement_namespace_addition_referenced")
    # Repeat the exact added-file proof after the source reads.
    file(root, scope, proof)
    return reduced
