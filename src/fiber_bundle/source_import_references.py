"""Explicit, frozen Markdown attachment mapping to archived imports only."""

import json
import sqlite3
from typing import Any
from urllib.parse import unquote

from . import auth, objects, vaults
from .config import Config, InvalidInput
from .inputs import text
from .note_transfer_links import ATTRIBUTES, MARKDOWN, REFERENCE, TAGS, WIKI, mask
from .store import Store


def validate(
    config: Config, db: sqlite3.Connection, client_id: str, vault_id: str, mapping: object
) -> dict[str, dict[str, Any]]:
    if not isinstance(mapping, dict) or len(mapping) > 32:
        raise InvalidInput("invalid import attachment mapping")
    total = 0
    result = {}
    for key, identity in mapping.items():
        text(key, limit=2048)
        row = db.execute(
            "SELECT * FROM source_imports WHERE id=? AND client_id=? AND vault_id=?",
            (identity, client_id, vault_id),
        ).fetchone()
        if row is None or row["state"] != "archived":
            raise auth.Denied(404)
        # No recursive mapping or cycles: attachments themselves must be independent originals.
        if json.loads(row["request_json"]).get("references"):
            raise InvalidInput("nested attachment mapping unavailable")
        for permission in ("add", "original"):
            vaults.checked_vault(config, auth.grant(db, client_id, vault_id, permission))
        from .source_imports import epoch

        if epoch(db, client_id, vault_id) != row["permission_epoch"]:
            raise auth.Denied(404)
        Store(config).verify_receipt_locked(db, row["receipt_id"])
        plan = json.loads(row["plan_json"])
        total += plan["source_bytes"]
        if total > 32 * 1024 * 1024:
            raise InvalidInput("import attachment total limit")
        mime = db.execute(
            "SELECT media_type FROM version_assets WHERE asset_id=? AND "
            "version_id=(SELECT source_version_id FROM receipts WHERE job_id=?)",
            (row["asset_id"], row["receipt_id"]),
        ).fetchone()[0]
        extension = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "application/pdf": "pdf",
            "text/markdown": "md",
            "text/plain": "txt",
        }.get(mime, "bin")
        result[key] = dict(
            extension=extension,
            import_id=identity,
            receipt_id=row["receipt_id"],
            asset_id=row["asset_id"],
            sha256=plan["source_sha256"],
            byte_count=plan["source_bytes"],
            vault_id=vault_id,
        )
    return result


def bound(config: Config, db: sqlite3.Connection, run_id: str) -> dict[str, dict[str, Any]]:
    row = db.execute("SELECT * FROM source_imports WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        return {}
    return validate(
        config,
        db,
        row["client_id"],
        row["vault_id"],
        json.loads(row["request_json"]).get("references", {}),
    )


def rewrite(text_value: str, names: dict[str, str]) -> str:
    visible = mask(text_value)
    edits: list[tuple[int, int, str]] = []
    for pattern in (MARKDOWN, REFERENCE, WIKI):
        for match in pattern.finditer(visible):
            group = 1 if match[1] is not None else 2
            raw = match[group]
            target = raw.split("|", 1)[0].split("#", 1)[0]
            if unquote(target) in names:
                start, end = match.span(group)
                edits.append((start, start + len(target), names[unquote(target)]))
    for tag in TAGS.finditer(visible):
        for attribute in ATTRIBUTES.finditer(tag[0]):
            group = 2 if attribute[2] is not None else 3
            raw = attribute[group]
            if attribute[1].lower() in {"src", "href", "data"} and unquote(raw) in names:
                start, end = attribute.span(group)
                edits.append((tag.start() + start, tag.start() + end, names[unquote(raw)]))
    for start, end, replacement in sorted(edits, reverse=True):
        text_value = text_value[:start] + replacement + text_value[end:]
    return text_value


def files(
    config: Config, mapping: dict[str, dict[str, Any]]
) -> tuple[dict[str, bytes], dict[str, str]]:
    out = {}
    names = {}
    for key, item in mapping.items():
        # Controlled asset destination; caller's filename/path is never a host path.
        name = "assets/" + item["sha256"] + "." + item["extension"]
        data = objects.read_object(
            config.state_root,
            objects.object_relative(item["vault_id"], item["sha256"]),
            item["sha256"],
            item["byte_count"],
        )
        out[name] = data
        names[key] = name
    return out, names
