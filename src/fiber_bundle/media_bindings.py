"""Stored direct assets owned by one client, with independent add/query/original grants."""

import sqlite3
from typing import Any

from . import auth, objects, vaults
from .config import Config, IntegrityError, InvalidInput
from .media_candidate import MIMES, SUBTITLE_MIMES
from .media_contract import MAX_MEDIA_BYTES
from .media_subtitles import MAX_SUBTITLE_BYTES
from .store import Store


def source(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    receipt_id: str,
    asset_id: str,
    *,
    subtitle: bool = False,
    permission: str = "add",
) -> tuple[dict[str, Any], bytes]:
    row = db.execute(
        "SELECT r.source_version_id,s.vault_id FROM receipts r "
        "JOIN source_versions v ON v.id=r.source_version_id JOIN sources s ON s.id=v.source_id "
        "WHERE r.job_id=? AND r.owner_client_id=? AND r.state='stored'",
        (receipt_id, client_id),
    ).fetchone()
    if row is None:
        raise auth.Denied(404)
    vaults.checked_vault(config, auth.grant(db, client_id, row["vault_id"], permission))
    Store(config).verify_receipt_locked(db, receipt_id)
    rows = db.execute(
        "SELECT a.*,va.media_type,va.role FROM assets a JOIN version_assets va ON va.asset_id=a.id "
        "WHERE va.version_id=? AND a.id=? AND a.vault_id=?",
        (row["source_version_id"], asset_id, row["vault_id"]),
    ).fetchall()
    types = SUBTITLE_MIMES if subtitle else MIMES
    limit = MAX_SUBTITLE_BYTES if subtitle else MAX_MEDIA_BYTES
    if (
        not rows
        or len({r["media_type"] for r in rows}) != 1
        or any(
            r["media_type"] not in types or r["role"] not in {"body", "attachment"} for r in rows
        )
    ):
        raise InvalidInput("media_explicit_asset_required")
    asset = rows[0]
    if not 0 < asset["byte_count"] <= limit:
        raise InvalidInput("media_asset_size")
    location = objects.object_relative(row["vault_id"], asset["sha256"])
    if location != asset["relative_path"]:
        raise IntegrityError("media_asset_location")
    data = objects.read_object(config.state_root, location, asset["sha256"], asset["byte_count"])
    return {
        "receipt_id": receipt_id,
        "asset_id": asset_id,
        "version_id": row["source_version_id"],
        "vault_id": row["vault_id"],
        "sha256": asset["sha256"],
        "byte_count": len(data),
        "media_type": asset["media_type"],
        "format": types[asset["media_type"]],
    }, data


def authorize(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    bound: dict[str, Any],
    *,
    permission: str = "add",
) -> tuple[bytes, bytes | None]:
    original, data = source(
        config,
        db,
        client_id,
        bound["source"]["receipt_id"],
        bound["source"]["asset_id"],
        permission=permission,
    )
    if original != bound["source"]:
        raise IntegrityError("media_source_binding_changed")
    subtitle = None
    if bound["subtitle"] is not None:
        actual, subtitle = source(
            config,
            db,
            client_id,
            bound["subtitle"]["receipt_id"],
            bound["subtitle"]["asset_id"],
            subtitle=True,
            permission=permission,
        )
        if actual != bound["subtitle"] or actual["vault_id"] != original["vault_id"]:
            raise IntegrityError("media_subtitle_binding_changed")
    return data, subtitle
