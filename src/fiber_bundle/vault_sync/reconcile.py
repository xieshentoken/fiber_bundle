"""Recognize a working note revision without rewriting its source, body or MOC."""

import json
from dataclasses import asdict, replace
from typing import Any

from .. import (
    auth,
    database,
    note_identity_store,
    note_index,
    publication_locations,
    vault_scope,
    vaults,
)
from ..config import Config, IntegrityError
from ..index_read_lease import Lease
from ..inputs import canonical
from . import scanner, store


def file_key(signature: str) -> tuple[int, int, str]:
    value = json.loads(signature)
    return value[0], value[1], float(value[7]).hex()


def continuous_save(config: Config, vault: str, path: str, old_key: tuple[Any, ...]) -> bool:
    with store.connection(config) as db:
        if db is None:
            return False
        changes = db.execute(
            "SELECT * FROM revisions WHERE vault=? AND path=? ORDER BY revision DESC LIMIT 2",
            (vault, path),
        ).fetchall()
        if not changes:
            return False
        change = changes[0]
        # A rename/replace observed after the baseline may be an editor save. An explicit
        # deletion, lost-event reconciliation or pre-existing mismatch remains ambiguous.
        if (
            not change["old_signature"]
            or not change["new_signature"]
            or file_key(change["old_signature"]) != old_key
            or not change["flags"] & 4
            or change["flags"] & (1 | 8)
        ):
            return False
        return not any(
            file_key(row[0]) == old_key
            for row in db.execute(
                "SELECT signature FROM files WHERE vault=? AND path<>? AND directory=0",
                (vault, path),
            )
        )


def current(config: Config, client: str, vault_id: str, relative: str) -> note_index.Note:
    """Persist an idempotent metadata-only intent, then CAS the two existing identity ledgers."""
    with Lease(config, vault_id) as lease:
        with database.connect(config.state_root) as main:
            vault = auth.grant(main, client, vault_id, "query")
            root = lease.read(lambda: vaults.checked_vault(config, vault))
            scope = vault_scope.for_vault(root, vault)
            from ..organization.packages import roots

            if (
                not relative.lower().endswith(".md")
                or not scope.allows(relative)
                or any(vault_scope.inside(relative, prefix) for prefix in roots(config, vault_id))
            ):
                raise IntegrityError("vault_sync_note_outside_scope")
            note: note_index.Note = lease.read(
                lambda: note_index.read_note(root, relative, note_index.Limits().note_bytes)
            )
            observed = scanner.signature((root / relative).lstat())
            with store.connection(config) as side:
                row = (
                    side.execute(
                        "SELECT * FROM files WHERE vault=? AND path=?", (vault_id, relative)
                    ).fetchone()
                    if side
                    else None
                )
            if row is None or row["signature"] != observed:
                raise IntegrityError("vault_sync_source_changed")
            registrations = note_identity_store.records(config, vault_id)
            before = next((r for r in registrations if r.relative_path == relative), None)
            managed = publication_locations.inventory(main, vault_id)[0].get(relative)
            if managed and managed["kind"] == "expert":
                raise IntegrityError("vault_sync_expert_requires_review")
            expected = (
                before.file_key if before else tuple(managed["file_key"]) if managed else None
            )
            identity = before.identity if before else managed["identity"] if managed else None
            intent = {
                "identity": identity,
                "file_key": note.file_key,
                "observed": observed,
                "before": asdict(before) if before else None,
                "managed": managed,
                "sha256": note.sha256,
            }
            with store.connection(config, write=True) as side:
                assert side is not None
                existing = side.execute(
                    "SELECT value FROM working_versions WHERE vault=? AND path=? AND revision=?",
                    (vault_id, relative, row["revision"]),
                ).fetchone()
                if existing:
                    frozen = json.loads(existing[0])
                    from .working_intent import validate

                    root_info = root.stat()
                    validate(frozen, intent, (root_info.st_dev, root_info.st_ino))
                    intent = frozen
            if expected is not None and note.file_key != expected and not existing:
                if not continuous_save(config, vault_id, relative, expected):
                    raise IntegrityError("vault_sync_replacement_needs_review")
            if before and managed and before.identity != managed["identity"]:
                raise IntegrityError("vault_sync_identity_conflict")
            with store.connection(config, write=True) as side:
                assert side is not None
                side.execute(
                    "INSERT OR IGNORE INTO working_versions VALUES(?,?,?,?,?,'prepared')",
                    (vault_id, relative, row["revision"], note.sha256, canonical(intent)),
                )
            if scanner.signature((root / relative).lstat()) != observed:
                raise IntegrityError("vault_sync_source_changed")
            if before and (before.sha256 != note.sha256 or before.file_key != note.file_key):
                if note.file_key is None:
                    raise IntegrityError("vault_sync_identity_unavailable")
                after = replace(
                    before,
                    sha256=note.sha256,
                    file_key=note.file_key,
                    revision=note_identity_store.next_revision(config, vault_id, before.identity),
                )
                note_identity_store.save(
                    config, vault_id, client, after, expected_revision=before.revision
                )
            if managed and list(note.file_key or ()) != managed["file_key"]:
                path_key = json.dumps(relative, ensure_ascii=False)
                with database.transaction(main):
                    result = main.execute(
                        "UPDATE publication_locations SET revision=revision+1,value=json_set(value,"
                        "?,json(?),?,json(?)) WHERE vault_id=? AND kind=? AND publication_id=? "
                        "AND json_extract(value,?)=json(?)",
                        (
                            "$.file_keys." + path_key,
                            canonical(note.file_key),
                            "$.claims." + path_key,
                            canonical(note.file_key[:2] if note.file_key else []),
                            vault_id,
                            managed["kind"],
                            managed["publication_id"],
                            "$.file_keys." + path_key,
                            canonical(managed["file_key"]),
                        ),
                    )
                    if result.rowcount != 1:
                        raise IntegrityError("vault_sync_location_changed")
            with store.connection(config, write=True) as side:
                assert side is not None
                side.execute(
                    "UPDATE working_versions SET state='complete' WHERE vault=? AND path=? "
                    "AND revision=?",
                    (vault_id, relative, row["revision"]),
                )
                side.execute("UPDATE libraries SET bodies=bodies+1 WHERE id=?", (vault_id,))
            return replace(
                note,
                identity=identity,
                publication_id=managed["publication_id"]
                if managed and managed["kind"] == "article"
                else None,
                publication_path=relative.rsplit("/", 1)[0]
                if managed and managed["kind"] == "article"
                else None,
            )
