"""Select verified source assets owned by an exact client/Vault; keep originals unchanged."""

import io
import sqlite3
import zipfile
from typing import Any

from . import asset_projection, auth, feishu_archives, vaults
from .config import Config, InvalidInput
from .inputs import AssetInput
from .material_contract import MIMES
from .operations import original
from .weixin_task_inputs import InputPending, _article


def kind(data: bytes, name: str, mime: str) -> tuple[str, str]:
    if mime in {"text/plain", "text/uri-list"}:
        try:
            _article([AssetInput(data, name, "text/plain", "body")])
            return "article", "text/uri-list"
        except InputPending:
            if mime == "text/uri-list":
                return "unsupported", mime
        try:
            if data.decode("utf-8-sig").strip():
                return "document", "text/plain"
        except UnicodeError:
            pass
        return "unsupported", mime
    detected = feishu_archives.media_type(data)
    if detected == "application/pdf":
        return "document", detected
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = archive.namelist()
                if len(names) > 100:
                    return "unsupported", mime
                for prefix, office in (("word/", "docx"), ("xl/", "xlsx"), ("ppt/", "pptx")):
                    if "[Content_Types].xml" in names and any(n.startswith(prefix) for n in names):
                        return "document", next(m for m, f in MIMES.items() if f == office)
            feishu_archives.assets(data, "source.zip")
            return "document", "application/zip"
        except (InvalidInput, zipfile.BadZipFile):
            return "unsupported", mime
    if detected.startswith("image/"):
        return "ocr_required", detected
    return "unsupported", mime


def listing(
    config: Config, db: sqlite3.Connection, client: str, vault: str
) -> list[dict[str, Any]]:
    vaults.checked_vault(config, auth.grant(db, client, vault, "add"))
    auth.grant(db, client, vault, "original")
    result = []
    for receipt in db.execute(
        "SELECT r.job_id,r.source_version_id FROM receipts r JOIN source_versions v ON "
        "v.id=r.source_version_id JOIN sources s ON s.id=v.source_id WHERE "
        "r.owner_client_id=? AND s.vault_id=? AND r.state='stored' AND r.event_id NOT "
        "LIKE 'source-task:%' ORDER BY r.created_at DESC LIMIT 50",
        (client, vault),
    ):
        assets, mapping = asset_projection.load(config, db, vault, receipt["source_version_id"])
        # A ZIP is one exact processing input; its members remain accessible as originals.
        zipped = any(a["media_type"] == "application/zip" for a in assets)
        for asset in assets:
            if asset["role"] == "event" or (
                zipped and asset["original_name"].startswith("zip-member-")
            ):
                continue
            data = original(
                config,
                db,
                client,
                vault,
                receipt["source_version_id"],
                asset["id"],
                8 * 1024 * 1024,
            )
            route, mime = kind(data, asset["original_name"], asset["media_type"])
            result.append(
                {
                    "receipt_id": receipt["job_id"],
                    "source_version_id": receipt["source_version_id"],
                    "asset_id": asset["id"],
                    "sha256": asset["sha256"],
                    "name": asset["download_name"],
                    "kind": route,
                    "media_type": mime,
                    "archive_projection": mapping,
                }
            )
    return result[:100]
