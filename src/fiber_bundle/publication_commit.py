"""Exclusive file-set journal. A completed file is never overwritten or silently adopted."""

import base64
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from . import auth, cloud_files, publication_locations, vault_directories, vault_save_policy, vaults
from .config import Config, IntegrityError, contained
from .inputs import canonical
from .objects import digest


def save(db: sqlite3.Connection, row: dict[str, Any], state: str) -> None:
    db.execute(
        "UPDATE publication_locations SET value=?,state=? WHERE kind=? AND publication_id=?",
        (canonical(row["plan"]), state, row["kind"], row["publication_id"]),
    )


def check(
    config: Config, db: sqlite3.Connection, row: dict[str, Any], client: str
) -> tuple[Path, Any]:
    from .intake_abort_publication import guard_publication

    guard_publication(config, db, row)
    grant = auth.grant(db, client, row["vault_id"], "add")
    root, scope, vault = vault_directories.vault_root(config, db, row["vault_id"])
    if (
        vault["read_only"]
        or vaults.checked_vault(config, grant) != root
        or vault_directories.key(root) != row["plan"]["root_key"]
    ):
        raise IntegrityError("layout_vault_changed")
    policy = vault_save_policy.get(config, db, row["vault_id"])
    current = db.execute(
        "SELECT value FROM vault_save_policies WHERE vault_id=?", (row["vault_id"],)
    ).fetchone()
    if (
        not policy["publication_ready"]
        or policy["revision"] != row["plan"]["policy_revision"]
        or not current
        or json.loads(current[0]) != row["plan"]["policy_binding"]
    ):
        raise IntegrityError("layout_policy_changed")
    for path in row["plan"]["outputs"]:
        vaults.require_scope(config, grant, path)
    return root, scope


def directory(
    config: Config,
    db: sqlite3.Connection,
    row: dict[str, Any],
    root: Path,
    scope: Any,
    relative: str,
) -> None:
    parent = ""
    frozen = row["plan"]["policy_binding"]["directories"]
    for component in relative.split("/") if relative else []:
        path = parent + "/" + component if parent else component
        expected = frozen.get(path) or row["plan"]["directories"].get(path)
        with vault_directories.opened_parent(
            root, scope, parent, internal_root=internal_root(row)
        ) as (_, fd):
            if expected is None:
                os.mkdir(component, mode=0o700, dir_fd=fd)
                os.fsync(fd)
                row["plan"]["directories"][path] = vault_directories.key(contained(root, path))
                save(db, row, "committing")
            elif vault_directories.key(contained(root, path)) != expected:
                raise IntegrityError("layout_directory_changed")
        parent = path


def internal_root(row: dict[str, Any]) -> str | None:
    # Only the full expert publisher owns this hidden evidence namespace.
    if row["kind"] == "expert" and row["package_root"]:
        return str(row["package_root"]) + "/.fiber-expert-canonical"
    return None


def _apply(
    config: Config,
    db: sqlite3.Connection,
    kind: str,
    identity: str,
    client: str,
    *,
    finalize: bool = True,
) -> None:
    row = publication_locations.get(db, kind, identity)
    if row is None:
        raise IntegrityError("layout_intent_missing")
    if row["state"] == "published":
        return
    try:
        root, scope = check(config, db, row, client)
        save(db, row, "committing")
        # Assets first. No partial body is visible to the application until both ledgers commit.
        ordered = sorted(
            row["plan"]["outputs"],
            key=lambda p: (p.endswith(".md"), p == row["entry_note_path"], p),
        )
        for path in ordered:
            check(config, db, row, client)
            parent = path.rsplit("/", 1)[0] if "/" in path else ""
            directory(config, db, row, root, scope, parent)
            payload = base64.b64decode(row["plan"]["outputs"][path], validate=True)
            with vault_directories.opened_parent(
                root, scope, parent, internal_root=internal_root(row)
            ) as (_, fd):
                name = path.rsplit("/", 1)[-1]
                claimed = row["plan"]["claims"].get(path)
                if claimed is None:
                    handle = os.open(
                        name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
                    )
                    try:
                        info = os.fstat(handle)
                        row["plan"]["claims"][path] = [info.st_dev, info.st_ino]
                        save(db, row, "committing")
                        with os.fdopen(handle, "wb", closefd=False) as stream:
                            stream.write(payload)
                            stream.flush()
                            os.fsync(handle)
                    finally:
                        os.close(handle)
                    os.fsync(fd)
                cloud_files.require_available(contained(root, path))
                from .publication_file_io import read

                raw, key = read(root, path)
                if (
                    key[:2] != row["plan"]["claims"][path]
                    or digest(raw)
                    != row["plan"].get("write_hashes", row["plan"]["published_hashes"])[path]
                ):
                    raise IntegrityError("layout_file_requires_reconciliation")
                row["plan"]["file_keys"][path] = key
                save(db, row, "committing")
        check(config, db, row, client)
        save(db, row, "published" if finalize else "committing")
    except BaseException:
        save(db, row, "reconcile")
        raise


def apply(
    config: Config,
    db: sqlite3.Connection,
    kind: str,
    identity: str,
    client: str,
    *,
    finalize: bool = True,
) -> None:
    from .intake_abort_store import critical

    with critical(config):
        _apply(config, db, kind, identity, client, finalize=finalize)
