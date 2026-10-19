"""Supplemental per-version names and ZIP references, never rewriting stored metadata."""

import sqlite3
from typing import Any

from . import objects
from .archive_members import project
from .config import Config, InvalidInput
from .download_names import filename
from .weixin_events import MAX_BYTES


def load(
    config: Config, db: sqlite3.Connection, vault_id: str, version_id: str
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    rows = [
        dict(r)
        for r in db.execute(
            "SELECT a.id,a.sha256,a.byte_count,va.original_name,va.media_type,va.role "
            "FROM version_assets va JOIN assets a ON a.id=va.asset_id "
            "WHERE va.version_id=? AND a.vault_id=? ORDER BY va.ordinal",
            (version_id, vault_id),
        )
    ]
    for row in rows:
        row["download_name"] = filename(row["original_name"], row["media_type"])
    projection = None
    archives = [r for r in rows if r["media_type"] == "application/zip"]
    if len(archives) == 1 and archives[0]["byte_count"] <= MAX_BYTES:
        archive = archives[0]
        try:
            raw = objects.read_object(
                config.state_root,
                objects.object_relative(vault_id, archive["sha256"]),
                archive["sha256"],
                archive["byte_count"],
            )
            projection = project(raw, rows)
            for member in projection["members"]:
                for row in rows:
                    if row["id"] == member["asset_id"]:
                        row["download_name"] = member["download_name"]
        except InvalidInput:
            projection = {"state": "unresolved", "reason": "unsupported_archive"}
    elif archives:
        projection = {"state": "unresolved", "reason": "ambiguous_or_large_archive"}
    return rows, projection
