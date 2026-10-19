"""Journaled same-Vault original copies. Immutable objects remain the source of truth.

Caller holds the storage lock; no network, extraction, macro execution or model call.
"""

import json
import os
import sqlite3
import time
from pathlib import PurePosixPath
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from . import auth, operations, publication_file_io, vault_directories, vault_save_policy, vaults
from .config import Config, IntegrityError
from .inputs import canonical
from .objects import digest
from .publication_layout import available, name
from .publication_role_layout import IMAGES

LIMIT = 128 * 1024 * 1024


def epoch(db: sqlite3.Connection, client: str, vault: str) -> int:
    row = db.execute(
        "SELECT revision FROM graph_permission_epochs WHERE client_id=? AND vault_id=?",
        (client, vault),
    ).fetchone()
    if row is None:
        raise auth.Denied(404)
    return int(row[0])


def project(
    config: Config, db: sqlite3.Connection, client: str, vault: str, version: str, asset: str
) -> dict[str, Any]:
    """Idempotent copy, never adopt/overwrite a manually changed file or stale permission."""
    grant = auth.grant(db, client, vault, "add")
    auth.grant(db, client, vault, "original")
    root, scope, _ = vault_directories.vault_root(config, db, vault)
    policy = vault_save_policy.get(config, db, vault)
    if not policy["publication_ready"] or policy["settings"].get("policy_version") != 2:
        return {"state": "not_configured", "original_saved": True, "projection_written": False}
    raw = operations.original(config, db, client, vault, version, asset, LIMIT)
    original_name, mime = operations.original_metadata(config, db, vault, version, asset)
    identity = str(uuid5(NAMESPACE_URL, f"fiber-original:{vault}:{version}:{asset}"))
    old = db.execute(
        "SELECT * FROM vault_operations WHERE id=? AND kind='attachment_projection'", (identity,)
    ).fetchone()
    if old:
        value = json.loads(old["payload"])
    else:
        settings = policy["settings"]
        suffix = PurePosixPath(original_name).suffix.lower()
        parent = (
            settings["image_dir"]
            if suffix in IMAGES and mime.startswith("image/")
            else settings["original_dir"]
        )
        path = available(
            root,
            parent,
            name(PurePosixPath(original_name).stem, "original")
            + "-"
            + digest((version + asset).encode())[:12],
            suffix,
            identity,
            set(),
        )
        value = {
            "schema": "attachment-projection/v1",
            "client_id": client,
            "vault_id": vault,
            "source_version_id": version,
            "asset_id": asset,
            "original_name": original_name,
            "media_type": mime,
            "bytes": len(raw),
            "sha256": digest(raw),
            "path": path,
            "root_key": vault_directories.key(root),
            "policy_revision": policy["revision"],
            "permission_epoch": epoch(db, client, vault),
            "claim": None,
        }
        now = time.time()
        db.execute(
            "INSERT INTO vault_operations VALUES(?,?,?,'prepared',?,?,?)",
            (identity, vault, "attachment_projection", canonical(value), now, now),
        )

    def save(state: str) -> None:
        db.execute(
            "UPDATE vault_operations SET state=?,payload=?,updated_at=? WHERE id=?",
            (state, canonical(value), time.time(), identity),
        )

    try:
        if (
            value["client_id"] != client
            or value["permission_epoch"] != epoch(db, client, vault)
            or value["root_key"] != vault_directories.key(root)
            or value["policy_revision"] != policy["revision"]
            or value["sha256"] != digest(raw)
        ):
            raise IntegrityError("attachment_projection_binding_changed")
        vaults.require_scope(config, grant, value["path"])
        if value["claim"] is None:
            save("applying")
            with vault_directories.opened_parent(root, scope, value["path"].rsplit("/", 1)[0]) as (
                _,
                fd,
            ):
                handle = os.open(
                    value["path"].rsplit("/", 1)[-1],
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=fd,
                )
                try:
                    info = os.fstat(handle)
                    value["claim"] = [info.st_dev, info.st_ino]
                    save("applying")
                    with os.fdopen(handle, "wb", closefd=False) as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(handle)
                finally:
                    os.close(handle)
                os.fsync(fd)
        written, key = publication_file_io.read(root, value["path"])
        if key[:2] != value["claim"] or digest(written) != value["sha256"]:
            raise IntegrityError("attachment_projection_copy_diverged")
        auth.grant(db, client, vault, "add")
        auth.grant(db, client, vault, "original")
        value["file_key"] = key
        save("published")
        return {
            "operation_id": identity,
            "state": "projected",
            "original_saved": True,
            "projection_written": True,
            **value,
        }
    except BaseException:
        save("reconcile")
        raise


def hidden_roots(db: sqlite3.Connection, vault: str) -> set[str]:
    """Exclude attachment namespaces even after a policy is disabled or replaced."""
    result = set()
    current = db.execute(
        "SELECT value FROM vault_save_policies WHERE vault_id=? AND state='active'", (vault,)
    ).fetchone()
    values = [json.loads(current[0])] if current else []
    values.extend(
        json.loads(row[0])["policy_binding"]
        for row in db.execute("SELECT value FROM publication_locations WHERE vault_id=?", (vault,))
    )
    for value in values:
        if value["settings"].get("policy_version") == 2:
            result.add(value["settings"]["attachment_root"])
    for row in db.execute(
        "SELECT payload FROM vault_operations WHERE vault_id=? AND kind='attachment_projection'",
        (vault,),
    ):
        result.add(json.loads(row[0])["path"].rsplit("/", 1)[0])
    return result


def archive_version(
    config: Config, db: sqlite3.Connection, client: str, vault: str, version: str
) -> list[dict[str, Any]]:
    """Initial archival hook; failed projection does not erase a successful original receipt."""
    from .config import StorageError

    policy = vault_save_policy.get(config, db, vault)
    if not policy["publication_ready"] or policy["settings"].get("policy_version") != 2:
        return []
    rows = db.execute(
        "SELECT asset_id FROM version_assets WHERE version_id=? ORDER BY ordinal LIMIT 1001",
        (version,),
    ).fetchall()
    if len(rows) > 1000:
        raise IntegrityError("attachment_projection_asset_limit")
    result = []
    for row in rows:
        try:
            result.append(project(config, db, client, vault, version, row[0]))
        except (StorageError, auth.Denied, OSError):
            identity = str(uuid5(NAMESPACE_URL, f"fiber-original:{vault}:{version}:{row[0]}"))
            asset_row = db.execute(
                "SELECT a.sha256,a.byte_count,v.original_name,v.media_type FROM version_assets v "
                "JOIN assets a ON a.id=v.asset_id WHERE v.version_id=? AND v.asset_id=?",
                (version, row[0]),
            ).fetchone()
            if (
                asset_row is not None
                and not db.execute(
                    "SELECT 1 FROM vault_operations WHERE id=?", (identity,)
                ).fetchone()
            ):
                failure = {
                    "source_version_id": version,
                    "asset_id": row[0],
                    "path": None,
                    "original_name": asset_row["original_name"],
                    "media_type": asset_row["media_type"],
                    "sha256": asset_row["sha256"],
                    "bytes": asset_row["byte_count"],
                }
                now = time.time()
                db.execute(
                    "INSERT OR REPLACE INTO vault_operations VALUES(?,?,?,'reconcile',?,?,?)",
                    (
                        identity + ":status",
                        vault,
                        "attachment_projection_status",
                        canonical(failure),
                        now,
                        now,
                    ),
                )
            result.append(
                {
                    "asset_id": row[0],
                    "state": "needs_review",
                    "original_saved": True,
                    "projection_written": False,
                    "code": "attachment_projection_requires_reconciliation",
                }
            )
    return result


def receipt_status(db: sqlite3.Connection, receipt: str | None) -> list[dict[str, Any]]:
    if not receipt:
        return []
    version = db.execute(
        "SELECT source_version_id FROM receipts WHERE job_id=?", (receipt,)
    ).fetchone()
    if not version or not version[0]:
        return []
    return [
        {
            "operation_id": row["id"],
            "state": "projected" if row["state"] == "published" else row["state"],
            **{
                key: value[key]
                for key in ["asset_id", "original_name", "media_type", "bytes", "sha256", "path"]
            },
        }
        for row in db.execute(
            "SELECT * FROM vault_operations WHERE kind IN ('attachment_project"
            "ion','attachment_projection_status') AND json_extract(payload,'$."
            "source_version_id')=? AND (kind='attachment_projection' OR NOT EX"
            "ISTS(SELECT 1 FROM vault_operations p WHERE p.id=replace(vault_op"
            "erations.id,':status',''))) ORDER BY created_at,id LIMIT 1000",
            (version[0],),
        )
        for value in [json.loads(row["payload"])]
    ]
