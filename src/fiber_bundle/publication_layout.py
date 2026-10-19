"""Logical reviewed files to frozen, collision-safe Obsidian locations; no file writes."""

import base64
import json
import re
import sqlite3
import time
import unicodedata
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import (
    publication_links,
    publication_locations,
    publication_note_roles,
    vault_directories,
    vault_save_policy,
)
from .config import Config, IntegrityError, contained
from .inputs import canonical
from .objects import digest

IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".avif"}


def name(value: str, fallback: str) -> str:
    value = unicodedata.normalize("NFC", value)
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f\[\]#^]', " ", value)
    value = " ".join(value.split()).strip(" .") or fallback
    while len(value.encode()) > 160:
        value = value[:-1]
    return value


def title(files: dict[str, bytes], fallback: str) -> str:
    text = files["note.md"].decode("utf-8-sig")
    for line in text.splitlines()[:60]:
        if line.startswith("title: "):
            raw = line[7:].strip()
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, str):
                    return name(parsed, fallback)
            except ValueError:
                pass
            return name(raw.strip("\"'"), fallback)
        if line.startswith("# "):
            return name(line[2:], fallback)
    return name(fallback, fallback)


def available(
    root: Path, parent: str, stem: str, suffix: str, identity: str, claimed: set[str]
) -> str:
    from .vault_scope import Scope

    candidates = [
        stem + suffix,
        *[
            stem + "-" + digest(identity.encode())[:10] + ("-" + str(i) if i else "") + suffix
            for i in range(100)
        ],
    ]
    wanted = {unicodedata.normalize("NFC", candidate).casefold() for candidate in candidates}
    with vault_directories.opened_parent(root, Scope(), parent) as (_, fd):
        import os

        existing = set()
        with os.scandir(fd) as entries:
            for entry in entries:
                folded = unicodedata.normalize("NFC", entry.name).casefold()
                if folded in wanted:
                    existing.add(folded)
    existing.update(
        unicodedata.normalize("NFC", p.rsplit("/", 1)[-1]).casefold()
        for p in claimed
        if p.rsplit("/", 1)[0] == parent
        and unicodedata.normalize("NFC", p.rsplit("/", 1)[-1]).casefold() in wanted
    )
    for candidate in candidates:
        if unicodedata.normalize("NFC", candidate).casefold() not in existing:
            return parent + "/" + candidate
    raise IntegrityError("layout_name_conflict")


def prepare(
    config: Config,
    db: sqlite3.Connection,
    kind: str,
    identity: str,
    vault_id: str,
    files: dict[str, bytes],
    *,
    persist: bool = True,
    refresh_preview: bool = False,
    source_units: list[dict[str, Any]] | None = None,
    markdown_limits: dict[str, int] | None = None,
    expert_inbox: bool = False,
) -> dict[str, Any] | None:
    from .note_placement import guard

    guard(db, vault_id)
    existing = publication_locations.get(db, kind, identity)
    if existing:
        if not refresh_preview or existing["state"] != "prepared" or existing["plan"]["claims"]:
            return existing
        db.execute(
            "DELETE FROM publication_locations WHERE kind=? AND publication_id=?", (kind, identity)
        )
    policy = vault_save_policy.get(config, db, vault_id)
    legacy_preview = db.execute(
        "SELECT payload FROM vault_operations WHERE vault_id=? AND kind='publication_preview' "
        "AND state='prepared' AND json_extract(payload,'$.kind')=? "
        "AND json_extract(payload,'$.publication_id')=? ORDER BY created_at DESC LIMIT 1",
        (vault_id, kind, identity),
    ).fetchone()
    if legacy_preview and not refresh_preview:
        frozen_preview = json.loads(legacy_preview[0])
        root, _, _ = vault_directories.vault_root(config, db, vault_id)
        if (
            frozen_preview["revision"] != policy["revision"]
            or frozen_preview["root_key"] != vault_directories.key(root)
            or frozen_preview["expires_at"] < time.time()
        ):
            raise IntegrityError("layout_preview_changed")

    if policy["state"] != "active" or policy["settings"]["layout_mode"] != "obsidian_folders":
        return None
    if not policy["publication_ready"] or policy["read_only"] or policy["pending_operation"]:
        raise IntegrityError("layout_policy_reconfirmation_required")
    if (
        "note.md" not in files
        or len(files) > 4096
        or sum(map(len, files.values())) > 128 * 1024 * 1024
    ):
        raise IntegrityError("layout_file_set_limit")
    root, scope, _ = vault_directories.vault_root(config, db, vault_id)
    settings = policy["settings"]
    policy_row = db.execute(
        "SELECT value FROM vault_save_policies WHERE vault_id=?", (vault_id,)
    ).fetchone()
    frozen = json.loads(policy_row[0])
    claimed = set()
    for row in db.execute("SELECT value FROM publication_locations WHERE vault_id=?", (vault_id,)):
        claimed.update(json.loads(row[0])["outputs"])
    heading = title(files, kind + "-" + identity[:8])
    packaged = kind == "expert" or any(
        n != "note.md" and Path(n).suffix.lower() not in IMAGES | {".json"}
        for n in files
        if not n.startswith(".")
    )
    package = (
        available(
            root,
            settings["inbox_dir"],
            heading + ("-Draft" if kind == "expert" else "-资料包"),
            "",
            identity,
            claimed,
        )
        if packaged
        else None
    )
    entry = (
        package + "/note.md"
        if package
        else available(root, settings["inbox_dir"], heading, ".md", identity, claimed)
    )
    mapping = {}
    outputs = {}
    for logical, raw in files.items():
        contained(root, logical)  # Reject absolute/traversing logical names too.
        if package:
            mapping[logical] = package + "/" + logical
        elif logical == "note.md":
            mapping[logical] = entry
        elif Path(logical).suffix.lower() in IMAGES:
            suffix = Path(logical).suffix.lower()
            mapping[logical] = available(
                root,
                settings["image_dir"],
                digest((kind + ":" + identity).encode())[:12]
                + "-"
                + digest(logical.encode())[:8]
                + "-"
                + digest(raw)[:10],
                suffix,
                identity + logical,
                claimed | set(mapping.values()),
            )
    image_mapping = {n: p for n, p in mapping.items() if Path(n).suffix.lower() in IMAGES}
    unified = settings.get("policy_version") == 2
    bodies = {"note.md"}
    supplements: dict[str, str] = {}
    if unified:
        from .publication_role_layout import mapping as role_mapping

        entry, package, mapping, bodies, supplements = role_mapping(
            settings,
            kind,
            identity,
            heading,
            files,
            claimed,
            lambda parent, stem, suffix, origin, reserved: available(
                root, parent, stem, suffix, origin, reserved
            ),
            expert_inbox=expert_inbox,
        )
        image_mapping = mapping
    entry_edits: list[tuple[int, int, str]] = []
    body_projections = {}
    for logical, path in mapping.items():
        raw = files[logical]
        if kind != "expert" and (
            (unified and logical in bodies) or (not package and logical == "note.md")
        ):
            edits: list[tuple[int, int, str]] = []
            raw = publication_note_roles.navigation(raw, logical, supplements)
            raw = publication_links.images(
                raw.decode("utf-8"),
                image_mapping,
                path,
                settings["image_link_style"],
                edits_out=edits,
                logical_entry=logical,
            ).encode()
            if logical == "note.md":
                entry_edits = edits
            body_projections[logical] = {
                "reviewed_sha256": digest(files[logical]),
                "published_sha256": digest(raw),
            }
        outputs[path] = base64.b64encode(raw).decode()
    for path in outputs:
        if not scope.allows(path):
            raise IntegrityError("layout_scope_denied")
    if unified and markdown_limits is not None:
        from .document_split import SplitRequired

        sizes = [len(base64.b64decode(outputs[mapping[n]])) for n in bodies]
        if any(size > markdown_limits["markdown_bytes"] for size in sizes):
            raise SplitRequired("document_split_unit_limit")
        if sum(sizes) > markdown_limits["markdown_total_bytes"]:
            raise SplitRequired("document_split_total_limit")
    plan = {
        "layout_version": 2 if unified else 1,
        "policy_revision": policy["revision"],
        "policy_binding": frozen,
        "mapping": mapping,
        "outputs": outputs,
        "logical_hashes": {n: digest(b) for n, b in files.items()},
        "published_hashes": {p: digest(base64.b64decode(b)) for p, b in outputs.items()},
        "claims": {},
        "directories": {},
        "file_keys": {},
        "root_key": vault_directories.key(root),
        **({"expert_inbox": True} if kind == "expert" and expert_inbox else {}),
        **({"body_projections": body_projections, "body_files": sorted(bodies)} if unified else {}),
        **({"supplement_roles": supplements} if supplements else {}),
    }
    if source_units is not None:
        projected = (
            source_units
            if package and not unified
            else publication_links.byte_spans(
                files["note.md"].decode("utf-8"), entry_edits, source_units
            )
        )
        plan["source_spans"] = {
            "reviewed_note_sha256": digest(files["note.md"]),
            "published_note_sha256": digest(base64.b64decode(outputs[entry])),
            "reviewed_units": source_units,
            "published_units": projected,
            "scope": "reviewed_rendered_document" if package else "entry_note",
            "status": "controlled_layout_projection"
            if projected is not None
            else "offsets_unavailable",
        }
    if not persist:
        return {
            "kind": kind,
            "publication_id": identity,
            "vault_id": vault_id,
            "entry_note_path": entry,
            "package_root": package,
            "revision": 1,
            "state": "prepared",
            "plan": plan,
        }
    db.execute(
        "INSERT INTO publication_locations VALUES(?,?,?,?,?,1,'prepared',?)",
        (kind, identity, vault_id, entry, package, canonical(plan)),
    )
    return publication_locations.get(db, kind, identity)


def publish(
    config: Config,
    db: sqlite3.Connection,
    kind: str,
    identity: str,
    client_id: str,
    files: dict[str, bytes],
    *,
    defer_visibility: bool = False,
) -> dict[str, Any] | None:
    row = publication_locations.get(db, kind, identity)
    if row is None:
        return None
    if row["state"] == "published":
        return publication_locations.wire(db, kind, identity)
    if row["plan"]["logical_hashes"] != {n: digest(b) for n, b in files.items()}:
        raise IntegrityError("layout_reviewed_content_changed")
    if row["state"] != "published":
        from .publication_commit import apply

        apply(config, db, kind, identity, client_id, finalize=not defer_visibility)
    return publication_locations.wire(db, kind, identity)


def preview_result(
    db: sqlite3.Connection,
    kind: str,
    identity: str,
    legacy: str,
    *,
    vault_id: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    location = publication_locations.get(db, kind, identity)
    if location is None and vault_id and root:
        revision = db.execute(
            "SELECT revision FROM vault_save_policies WHERE vault_id=?", (vault_id,)
        ).fetchone()
        db.execute(
            "UPDATE vault_operations SET state='cancelled' WHERE kind='publication_preview' "
            "AND state='prepared' AND json_extract(payload,'$.kind')=? "
            "AND json_extract(payload,'$.publication_id')=?",
            (kind, identity),
        )
        now = time.time()
        value = {
            "kind": kind,
            "publication_id": identity,
            "revision": revision[0] if revision else 0,
            "root_key": vault_directories.key(root),
            "expires_at": now + 600,
        }
        db.execute(
            "INSERT INTO vault_operations VALUES(?,?,?,'prepared',?,?,?)",
            (str(uuid4()), vault_id, "publication_preview", canonical(value), now, now),
        )
    return {
        "kind": kind,
        "publication_id": identity,
        "entry_note_path": location["entry_note_path"] if location else legacy + "/note.md",
        "package_root": location["package_root"] if location else legacy,
        "layout_mode": "obsidian_folders" if location else "legacy_bundle",
        "paths": list(location["plan"]["outputs"]) if location else [legacy + "/note.md"],
        "state": "publication_preview",
        "vault_write_performed": False,
    }


def discard_preview(
    config: Config, db: sqlite3.Connection, kind: str, identity: str
) -> dict[str, Any]:
    """Cancel an untouched preview; a source ledger always takes precedence over cancellation."""
    row = publication_locations.get(db, kind, identity)
    if row is None:
        db.execute(
            "UPDATE vault_operations SET state='cancelled' WHERE kind='publication_preview' "
            "AND state='prepared' AND json_extract(payload,'$.kind')=? "
            "AND json_extract(payload,'$.publication_id')=?",
            (kind, identity),
        )
        return {"state": "cancelled", "vault_write_performed": False}
    if row["state"] != "prepared" or row["plan"]["claims"]:
        raise IntegrityError("layout_preview_already_started")
    tables = {
        "article": ("article_publications", "id"),
        "document": ("document_publications", "run_id"),
        "media": ("media_publications", "job_id"),
        "expert": ("source_expert_publications", "task_id"),
    }
    if kind in tables:
        table, column = tables[kind]
        if db.execute(f"SELECT 1 FROM {table} WHERE {column}=?", (identity,)).fetchone():
            raise IntegrityError("layout_preview_already_started")
    elif kind == "intake":
        from .channels import store

        with store.readonly(config) as side:
            rows = (
                side.execute("SELECT value FROM channel_intake_records LIMIT 1001").fetchall()
                if side
                else []
            )
        if len(rows) > 1000:
            raise IntegrityError("layout_preview_candidate_limit")
        if any(
            link.get("publication", {}).get("id") == identity
            for raw in rows
            for link in json.loads(raw[0]).get("links", [])
            if link.get("publication")
        ):
            raise IntegrityError("layout_preview_already_started")
    else:
        raise IntegrityError("layout_source_kind_unknown")
    db.execute(
        "DELETE FROM publication_locations WHERE kind=? AND publication_id=?", (kind, identity)
    )
    return {"state": "cancelled", "vault_write_performed": False}
