"""Expose publication archives under their original Vault's current capability."""

import sqlite3
from typing import Any

from . import auth


def visible(
    db: sqlite3.Connection, client: str, version: str, *, vault_hint: str | None = None
) -> list[dict[str, Any]]:
    if vault_hint:
        from .vault_merge_originals import mapping

        copied = mapping(db, version, vault_hint)
        if copied:
            version = copied["target_version_id"]
    from .document_creation_save import archive_visible

    if not archive_visible(db, client, version):
        return []
    source = db.execute(
        "SELECT s.vault_id FROM source_versions v JOIN sources s ON s.id=v.source_id WHERE v.id=?",
        (version,),
    ).fetchone()
    if source is None:
        return []
    vid = source[0]
    try:
        auth.grant(db, client, vid, "original")
    except auth.Denied:
        return []
    return [
        {
            "asset_id": a["id"],
            "name": a["original_name"],
            "url": f"/v1/vaults/{vid}/versions/{version}/assets/{a['id']}",
        }
        for a in db.execute(
            "SELECT a.id,va.original_name FROM version_assets va JOIN assets a ON a.id=va.asset_id "
            "WHERE va.version_id=? AND a.vault_id=? ORDER BY va.ordinal",
            (version, vid),
        )
    ]
