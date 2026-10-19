"""Authorized receipt and fixed-version operations over the existing storage code."""

import sqlite3
from typing import Any

from fiber_bundle import auth, objects, vaults
from fiber_bundle.config import Config, IntegrityError
from fiber_bundle.payloads import Intake, decode
from fiber_bundle.store import Receipt, Store, _receipt


def receipt_result(receipt: Receipt) -> dict[str, Any]:
    return {
        "job_id": receipt.job_id,
        "receipt_state": receipt.state,
        "source_version_id": receipt.source_version_id,
        "error_code": receipt.error_code,
        "published": False,
    }


def intake(config: Config, db: sqlite3.Connection, client_id: str, data: Intake) -> dict[str, Any]:
    vault = auth.grant(db, client_id, data.vault_id, "add")
    vaults.checked_vault(config, vault)
    scope = f"client:{client_id}"
    receipt = Store(config).ingest_locked(
        db,
        ingress_scope=scope,
        event_id=data.event_id,
        vault=vault["name"],
        source_key=f"{scope}:{data.source_key}",
        raw_event=decode(data.raw_event),
        items=tuple(asset.storage_input() for asset in data.assets),
        session_scope=scope,
        session_id=data.session_id,
        retention=data.retention,
        owner_client_id=client_id,
    )
    return receipt_result(receipt)


def receipt_status(
    config: Config, db: sqlite3.Connection, client_id: str | None, job_id: str
) -> dict[str, Any]:
    row = db.execute("SELECT * FROM receipts WHERE job_id=?", (job_id,)).fetchone()
    if row is None or (client_id is not None and row["owner_client_id"] != client_id):
        raise auth.Denied(404)
    store = Store(config)
    data = store._manifest(row)
    vault_id = store._vault_id(db, data["vault"])
    if client_id is not None:
        auth.grant(db, client_id, vault_id, "add")
    return receipt_result(_receipt(row))


def versions(
    config: Config, db: sqlite3.Connection, client_id: str, vault_id: str, offset: int, limit: int
) -> list[dict[str, Any]]:
    vaults.checked_vault(config, auth.grant(db, client_id, vault_id, "query"))
    rows = db.execute(
        "SELECT v.id,v.source_id,v.version_no,v.retention,v.created_at FROM source_versions v "
        "JOIN sources s ON s.id=v.source_id WHERE s.vault_id=? "
        "ORDER BY v.created_at,v.id LIMIT ? OFFSET ?",
        (vault_id, limit, offset),
    ).fetchall()
    result = []
    from .document_creation_save import archive_visible

    for row in rows:
        if not archive_visible(db, client_id, row["id"]):
            continue
        item = dict(row)
        from .asset_projection import load

        item["assets"], projection = load(config, db, vault_id, row["id"])
        if projection is not None:
            item["archive_projection"] = projection
        from .config import contained
        from .media_cleanup_store import cleaned

        for asset in item["assets"]:
            if cleaned(db, asset["id"]):
                path = db.execute(
                    "SELECT relative_path FROM assets WHERE id=?", (asset["id"],)
                ).fetchone()[0]
                if not contained(config.state_root, path).exists():
                    asset["retention_state"] = "cleaned_after_publication"
        result.append(item)
    return result


def original(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    vault_id: str,
    version_id: str,
    asset_id: str,
    limit: int,
) -> bytes:
    vaults.checked_vault(config, auth.grant(db, client_id, vault_id, "original"))
    from .document_creation_save import archive_visible

    if not archive_visible(db, client_id, version_id):
        raise auth.Denied(404)
    row = db.execute(
        "SELECT a.* FROM assets a JOIN version_assets va ON va.asset_id=a.id "
        "JOIN source_versions v ON v.id=va.version_id JOIN sources s ON s.id=v.source_id "
        "WHERE va.version_id=? AND a.id=? AND a.vault_id=? AND s.vault_id=?",
        (version_id, asset_id, vault_id, vault_id),
    ).fetchone()
    if row is None:
        raise auth.Denied(404)
    if row["byte_count"] > limit:
        raise auth.Denied(413)
    from .config import contained
    from .media_cleanup_store import cleaned

    if cleaned(db, asset_id) and not contained(config.state_root, row["relative_path"]).exists():
        raise auth.Denied(410)
    expected = objects.object_relative(vault_id, row["sha256"])
    if row["relative_path"] != expected:
        raise IntegrityError("invalid object location")
    data = objects.read_object(config.state_root, expected, row["sha256"], row["byte_count"])
    auth.grant(db, client_id, vault_id, "original")
    if not archive_visible(db, client_id, version_id):
        raise auth.Denied(404)
    return data


def original_metadata(
    config: Config, db: sqlite3.Connection, vault_id: str, version_id: str, asset_id: str
) -> tuple[str, str]:
    """Call only after original() authorizes and verifies the exact binding."""
    from .asset_projection import load

    assets, _ = load(config, db, vault_id, version_id)
    for asset in assets:
        if asset["id"] == asset_id:
            return asset["download_name"], asset["media_type"]
    raise auth.Denied(404)
