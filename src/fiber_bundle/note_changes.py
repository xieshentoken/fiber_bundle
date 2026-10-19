"""Metadata-only note snapshots and conservative comparisons. No storage or authorization."""

import re
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

from .config import IntegrityError, InvalidInput
from .note_index import Note

SCHEMA = "fiber-note-snapshot/v1"
MAX_NOTES = 10000
MAX_BYTES = 4 * 1024 * 1024
DIGEST = re.compile(r"[0-9a-f]{64}")


def identifier(value: str) -> str:
    try:
        if str(UUID(value)) != value:
            raise ValueError("noncanonical identity")
    except (ValueError, AttributeError) as exc:
        raise InvalidInput("invalid note snapshot scope identity") from exc
    return value


@dataclass(frozen=True)
class Entry:
    identity: str
    note_path: str
    sha256: str


def entries(notes: list[Note]) -> tuple[Entry, ...]:
    return tuple(
        sorted(
            (
                Entry(
                    note.identity
                    if note.identity is not None
                    else "publication:" + note.publication_id
                    if note.publication_id is not None
                    else "path:" + note.relative_path,
                    note.relative_path,
                    note.sha256,
                )
                for note in notes
            ),
            key=lambda note: note.identity,
        )
    )


def validate(value: object) -> tuple[Entry, ...]:
    if not isinstance(value, list) or len(value) > MAX_NOTES:
        raise IntegrityError("invalid note snapshot entries")
    result = []
    paths: set[str] = set()
    identities: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"identity", "note_path", "sha256"}:
            raise IntegrityError("invalid note snapshot entry")
        identity, path, digest = item["identity"], item["note_path"], item["sha256"]
        if (
            not all(isinstance(v, str) for v in (identity, path, digest))
            or not path
            or path.startswith("/")
            or "\\" in path
            or "\x00" in path
            or any(p in {"", ".", ".."} or p.startswith(".") for p in path.split("/"))
            or not DIGEST.fullmatch(digest)
            or path in paths
            or identity in identities
        ):
            raise IntegrityError("invalid note snapshot identity or path")
        if identity.startswith(("publication:", "note:")):
            identifier(identity.split(":", 1)[1])
        elif identity != "path:" + path:
            raise IntegrityError("invalid historical note identity")
        identities.add(identity)
        paths.add(path)
        result.append(Entry(identity, path, digest))
    if result != sorted(result, key=lambda note: note.identity):
        raise IntegrityError("note snapshot entries not canonical")
    return tuple(result)


def kind(before: Entry | None, after: Entry | None) -> str:
    if before is None:
        return "added"
    if after is None:
        return "missing"
    moved = before.note_path != after.note_path
    changed = before.sha256 != after.sha256
    return (
        "moved_and_edited"
        if moved and changed
        else "moved"
        if moved
        else "edited"
        if changed
        else "unchanged"
    )


def compare(before: tuple[Entry, ...], after: tuple[Entry, ...]) -> list[dict[str, Any]]:
    old = {note.identity: note for note in before}
    new = {note.identity: note for note in after}
    result = []
    for identity in sorted(old.keys() | new.keys()):
        status = kind(old.get(identity), new.get(identity))
        if status != "unchanged":
            result.append(
                {
                    "identity": identity,
                    "kind": status,
                    "before": asdict(old[identity]) if identity in old else None,
                    "current": asdict(new[identity]) if identity in new else None,
                }
            )
    return result


def check(before: tuple[Entry, ...], after: tuple[Entry, ...], identity: str) -> dict[str, Any]:
    expected = next((note for note in before if note.identity == identity), None)
    if expected is None:
        raise InvalidInput("identity is not in baseline snapshot")
    current = next((note for note in after if note.identity == identity), None)
    status = kind(expected, current)
    return {
        "status": "match" if status == "unchanged" else "conflict",
        "reason": status,
        "expected": asdict(expected),
        "current": asdict(current) if current is not None else None,
        "write_performed": False,
        "write_authorization": False,
    }
