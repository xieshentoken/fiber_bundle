"""Verify late registrations against frozen file evidence; keep real conflicts blocked."""

import json
from typing import Any

from ..config import IntegrityError


def validate(frozen: dict[str, Any], current: dict[str, Any], vault_key: tuple[int, int]) -> None:
    if frozen["observed"] != current["observed"] or frozen["sha256"] != current["sha256"]:
        raise IntegrityError("vault_sync_revision_conflict")
    before, original = current["before"], frozen["before"]
    managed, old_managed = current["managed"], frozen["managed"]
    if bool(managed) != bool(old_managed):
        raise IntegrityError("vault_sync_location_changed")

    if before and not original:
        # Registration can finish after observation. Accept only exact file/content and
        # provenance evidence; keep the immutable observation intent and registration ID.
        expected_publication = (
            managed["publication_id"] if managed and managed["kind"] == "article" else None
        )
        if (
            tuple(before["file_key"]) != tuple(current["file_key"])
            or tuple(before["vault_key"]) != vault_key
            or before["sha256"] != current["sha256"]
            or before["publication_id"] != expected_publication
            or (frozen["identity"] and before["identity"] != frozen["identity"])
        ):
            raise IntegrityError("vault_sync_identity_conflict")
    elif bool(before) != bool(original):
        raise IntegrityError("vault_sync_identity_conflict")
    elif before and (
        before["identity"] != original["identity"]
        or before["relative_path"] != original["relative_path"]
        or before["publication_id"] != original["publication_id"]
        or tuple(before["vault_key"]) != tuple(original["vault_key"])
        or tuple(before["file_key"])
        not in (tuple(original["file_key"]), tuple(current["file_key"]))
    ):
        raise IntegrityError("vault_sync_identity_conflict")
    if managed and (
        any(managed[k] != old_managed[k] for k in ("kind", "identity", "publication_id", "logical"))
        or managed["file_key"] not in (old_managed["file_key"], list(current["file_key"]))
    ):
        raise IntegrityError("vault_sync_location_changed")


def complete_read(
    config: Any, vault: str, path: str, revision: int, intent: dict[str, Any], source: Any
) -> None:
    """One durable commit when no identity/location CAS needs a prepared boundary.

    The intent and its final state are identical to the two-commit path. A
    crash can leave neither or both, because this path has no external writes.
    """
    from ..inputs import canonical
    from . import scanner, store

    with store.connection(config, write=True) as db:
        assert db is not None
        current = db.execute(
            "SELECT signature,revision FROM files WHERE vault=? AND path=?", (vault, path)
        ).fetchone()
        if (
            current is None
            or current["revision"] != revision
            or current["signature"] != intent["observed"]
        ):
            raise IntegrityError("vault_sync_source_changed")
        old = db.execute(
            "SELECT value FROM working_versions WHERE vault=? AND path=? AND revision=?",
            (vault, path, revision),
        ).fetchone()
        value = canonical(intent)
        if old is not None and canonical(json.loads(old[0])) != value:
            raise IntegrityError("vault_sync_revision_conflict")
        db.execute(
            "INSERT OR IGNORE INTO working_versions VALUES(?,?,?,?,?,'prepared')",
            (vault, path, revision, intent["sha256"], value),
        )
        if scanner.signature(source.lstat()) != intent["observed"]:
            raise IntegrityError("vault_sync_source_changed")
        db.execute(
            "UPDATE working_versions SET state='complete' WHERE vault=? AND path=? AND revision=?",
            (vault, path, revision),
        )
        db.execute("UPDATE libraries SET bodies=bodies+1 WHERE id=?", (vault,))
