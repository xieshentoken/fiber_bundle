"""Read-only proof of an archive whose exchanges, move and metadata already committed."""

import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import auth, cloud_observation, note_identity_store, publication_locations
from .config import Config, IntegrityError, contained
from .inputs import canonical
from .objects import digest
from .publication_file_io import read, tree


def changed(code: str, root: Path, path: str, fields: list[str]) -> IntegrityError:
    return cloud_observation.ObservationError(
        code, contained(root, path), "committed_file_changed", changed=fields
    )


def files(root: Path, scope: Any, value: dict[str, Any]) -> None:
    """Check actual targets and displaced originals without invoking a write primitive."""
    if not value.get("metadata_committed") or (
        value["action"] != "associate_only" and not value.get("move_applied")
    ):
        raise IntegrityError("placement_committed_evidence_missing")
    moved = value["action"] != "associate_only"
    if moved:
        source, target = (
            contained(root, value["source_root"]),
            contained(root, value["target_root"]),
        )
        if source.exists() or source.is_symlink() or not target.exists() or target.is_symlink():
            raise IntegrityError("placement_move_ambiguous")
        if len(value["source_files"]) > 1 and any(
            p.startswith(value["source_root"] + "/") for p in value["source_files"]
        ):
            if set(tree(root, value["target_root"], scope)) != {
                p.removeprefix(value["source_root"] + "/") for p in value["source_files"]
            }:
                raise IntegrityError("placement_package_members_changed")
    updates = {u["path"]: u for u in value["updates"]}
    for path, evidence in value["source_files"].items():
        actual = value["moves"][path] if moved else path
        raw, key = read(root, actual)
        update = updates.get(path)
        expected = value["exchanges"].get(path) if update else None
        wanted_key = expected["after_key"] if expected else evidence["file_key"][:2]
        wanted_hash = update["after_sha256"] if update else evidence["sha256"]
        if digest(raw) != wanted_hash or key[:2] != wanted_key:
            fields = (["sha256"] if digest(raw) != wanted_hash else []) + (
                ["inode"] if key[:2] != wanted_key else []
            )
            raise changed("placement_source_changed", root, actual, fields)
    for path, update in updates.items():
        exchange = value["exchanges"].get(path)
        if not exchange or exchange["state"] != "exchanged":
            raise IntegrityError("placement_committed_evidence_missing")
        raw, key = read(root, update["target"] if moved else path)
        before, before_key = read(root, exchange["stage"])
        if digest(raw) != update["after_sha256"] or key[:2] != exchange["after_key"]:
            fields = (["sha256"] if digest(raw) != update["after_sha256"] else []) + (
                ["inode"] if key[:2] != exchange["after_key"] else []
            )
            raise changed(
                "placement_note_changed", root, update["target"] if moved else path, fields
            )
        if digest(before) != update["before_sha256"] or before_key != update["file_key"]:
            raise changed("placement_backup_changed", root, exchange["stage"], ["backup"])


def verify(
    config: Config,
    db: sqlite3.Connection,
    value: dict[str, Any],
    root: Path,
    scope: Any,
    *,
    namespace_addition: Any = None,
) -> None:
    """The metadata flag selects a recovery phase; it never substitutes for proof."""
    from .archive_link_planning import verify as verify_links

    with cloud_observation.scope(root), cloud_observation.phase("archive_committed_verify"):
        actual_root = root.stat()
        root_key = value.get("root_key", value.get("fixed", {}).get("root_key"))
        if root_key != [actual_root.st_dev, actual_root.st_ino]:
            raise IntegrityError("placement_policy_or_vault_changed")
        grant = auth.grant(db, value["client_id"], value["vault_id"], "add")
        from .vaults import require_scope

        for path in (
            set(value["moves"])
            | set(value["moves"].values())
            | {u["path"] for u in value["updates"]}
        ):
            require_scope(config, grant, path)
        files(root, scope, value)
        if namespace_addition is not None:
            from .committed_namespace_addition import bind

            bind(config, db, value, root, scope, namespace_addition)
            verify_links(root, scope, value, namespace_addition=namespace_addition)
        else:
            verify_links(root, scope, value)
        registrations = {
            r.identity: r for r in note_identity_store.records(config, value["vault_id"])
        }
        updates = {u["path"]: u for u in value["updates"]}
        for before in value["registrations"]:
            old = before["relative_path"]
            path = value["moves"].get(old, old)
            if old not in updates and path == old:
                continue
            raw, key = read(root, path)
            expected = {
                **before,
                "relative_path": path,
                "sha256": digest(raw),
                "file_key": tuple(key),
                "vault_key": tuple(before["vault_key"]),
                "revision": before["revision"] + 1,
            }
            current = registrations.get(before["identity"])
            if current is None or asdict(current) != expected:
                raise IntegrityError("placement_registered_identity_changed")
        # New journals retain exact committed metadata. Older journals retain
        # their original location revision and registration checks at the caller.
        for expected in value.get("committed_locations", []):
            location = publication_locations.get(db, expected["kind"], expected["publication_id"])
            if location is None or digest(canonical(location).encode()) != expected["sha256"]:
                raise IntegrityError("placement_location_changed")
