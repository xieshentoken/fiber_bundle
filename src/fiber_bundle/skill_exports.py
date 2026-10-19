"""Frozen offline Skill snapshots in the existing operation journal; no model or Vault writes."""

import json
import posixpath
import sqlite3
import time
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field

from . import auth
from .agent_connections import knowledge, store
from .config import Config, IntegrityError, InvalidInput, contained, make_directory
from .inputs import canonical
from .objects import digest
from .payloads import Payload
from .query_policy_store import write_private
from .skill_export_content import MAX_BYTES, current, project


class Preview(Payload):
    connection_id: str = Field(min_length=1, max_length=100)
    vault_id: str = Field(min_length=1, max_length=100)
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,47}$")
    mode: Literal["selected", "vault", "moc"] = "selected"
    module_ids: list[str] = Field(default_factory=list, max_length=1000)
    include_originals: bool = False


def binding(config: Config, db: sqlite3.Connection, connection: str) -> tuple[str, str]:
    context = store.context(config, db, connection)
    if not context["enabled"]:
        raise auth.Denied(403)
    return store.get(db, connection)["client_id"], digest(canonical(context).encode())


def get(db: sqlite3.Connection, identity: str) -> dict[str, Any]:
    row = db.execute(
        "SELECT * FROM vault_operations WHERE kind='skill_export' AND id=?", (identity,)
    ).fetchone()
    if row is None:
        raise auth.Denied(404)
    return {**dict(row), "value": json.loads(row["payload"])}


def view(row: dict[str, Any]) -> dict[str, Any]:
    value = row["value"]
    from .processing_errors import MESSAGES

    return {
        "id": row["id"],
        "vault_id": row["vault_id"],
        "state": row["state"],
        "created_at": row["created_at"],
        "name": value["name"],
        "members": value["members"],
        "completed_members": len(value["completed"]),
        "bytes": value["bytes"],
        "estimated_content_bytes": value["estimated_content_bytes"],
        "known_gaps": value["known_gaps"],
        "include_originals": value["include_originals"],
        "preview_sha256": value["preview_sha256"],
        "package_sha256": value.get("package_sha256"),
        "code": value.get("error_code"),
        "message": MESSAGES.get(value.get("error_code")),
        "model_called": False,
        "external_model_cost": "unknown; managed by external host",
        "revocation": "Downloaded offline copies cannot be recalled.",
        "snapshot_version": row["id"],
    }


def save(db: sqlite3.Connection, row: dict[str, Any], state: str) -> None:
    db.execute(
        "UPDATE vault_operations SET state=?,payload=?,updated_at=? WHERE id=?",
        (state, canonical(row["value"]), time.time(), row["id"]),
    )
    row["state"] = state


def authorize(
    config: Config, db: sqlite3.Connection, row: dict[str, Any], *, sources: bool = True
) -> str:
    value = row["value"]
    cid, sha = binding(config, db, value["connection_id"])
    if sha != value["binding_sha256"]:
        raise InvalidInput("skill_export_permissions_changed")
    if value["include_originals"]:
        auth.grant(db, cid, row["vault_id"], "original")
    cache: dict[str, Any] = {}
    for member in value["members"]:
        current(config, db, cid, member, cache)
        if not sources:
            continue
        files, _ = project(
            config, db, cid, member, value["members"], value["include_originals"], cache
        )
        if (
            digest(canonical({n: digest(b) for n, b in files.items()}).encode())
            != member["projection_sha256"]
        ):
            raise InvalidInput("skill_export_source_changed")
    return cid


def preview(config: Config, db: sqlite3.Connection, request: Preview) -> dict[str, Any]:
    cid, sha = binding(config, db, request.connection_id)
    if request.include_originals:
        auth.grant(db, cid, request.vault_id, "original")
    items, offset = [], 0
    while True:
        packet = knowledge.catalog(config, db, cid, [request.vault_id], offset, 50)
        items.extend(packet["items"])
        if len(items) > 1000:
            raise InvalidInput("skill_export_capacity")
        if packet["next_offset"] is None:
            break
        offset = packet["next_offset"]
    chosen = set(request.module_ids)
    gaps = []
    if request.mode == "vault":
        chosen = {i["module_id"] for i in items}
    elif request.mode == "moc":
        if len(chosen) != 1:
            raise InvalidInput("skill_export_selection_required")
        moc = next((i for i in items if i["module_id"] in chosen and i["kind"] == "note"), None)
        if moc is None:
            raise auth.Denied(404)
        from urllib.parse import unquote, urlsplit

        from .skill_export_content import LINK, WIKI

        _, _, note, _ = current(config, db, cid, moc)
        from .note_transfer_links import mask

        visible = mask(note.text)
        paths = {i.get("note_path"): i["module_id"] for i in items}
        targets = [m[3] for m in LINK.finditer(visible)]
        targets += [m[2].split("|")[0] for m in WIKI.finditer(visible)]
        for target in targets:
            if urlsplit(target).scheme:
                continue
            path = posixpath.normpath(
                posixpath.join(posixpath.dirname(note.relative_path), unquote(target.split("#")[0]))
            )
            match = paths.get(path) or paths.get(path + ".md")
            if match:
                chosen.add(match)
            else:
                gaps.append("主题存在未选入或不可见引用；仅冻结直接引用，未递归扩展。")
    if not chosen or not chosen <= {i["module_id"] for i in items}:
        raise InvalidInput("skill_export_selection_required")
    members = [
        {k: i[k] for k in ("module_id", "version", "vault_id", "title", "kind")}
        | {
            k: i[k]
            for k in (
                "note_path",
                "completeness_status",
                "known_gaps",
                "artifact_stage",
                "review_state",
                "knowledge_verified",
            )
            if k in i
        }
        for i in items
        if i["module_id"] in chosen
    ]
    estimated = 0
    cache: dict[str, Any] = {}
    for member in members:
        files, missing = project(config, db, cid, member, members, request.include_originals, cache)
        member["projection_sha256"] = digest(
            canonical({n: digest(b) for n, b in files.items()}).encode()
        )
        estimated += sum(len(raw) for raw in files.values())
        gaps.extend(missing)
    if estimated > MAX_BYTES:
        raise InvalidInput("skill_export_capacity")
    value = {
        "connection_id": request.connection_id,
        "binding_sha256": sha,
        "name": request.name,
        "members": members,
        "include_originals": request.include_originals,
        "completed": [],
        "files": {},
        "known_gaps": sorted(set(gaps)),
        "bytes": 0,
        "estimated_content_bytes": estimated,
    }
    value["preview_sha256"] = digest(canonical(value).encode())
    identity, now = str(uuid4()), time.time()
    db.execute(
        "INSERT INTO vault_operations VALUES(?,?,'skill_export','preview',?,?,?)",
        (identity, request.vault_id, canonical(value), now, now),
    )
    return view(get(db, identity))


def folder(config: Config, identity: str) -> Path:
    return contained(config.state_root, "skill-exports/" + identity)


def step(
    config: Config, db: sqlite3.Connection, identity: str, confirmation: str
) -> dict[str, Any]:
    row = get(db, identity)
    value = row["value"]
    if confirmation != value["preview_sha256"] or row["state"] not in {
        "preview",
        "running",
        "failed",
    }:
        raise InvalidInput("skill_export_confirmation_required")
    try:
        cid = authorize(config, db, row, sources=False)
        root = folder(config, identity)
        make_directory(root)
        next_member = next(
            (m for m in value["members"] if m["module_id"] not in value["completed"]), None
        )
        if next_member:
            files, gaps = project(
                config, db, cid, next_member, value["members"], value["include_originals"]
            )
            if (
                digest(canonical({n: digest(b) for n, b in files.items()}).encode())
                != next_member["projection_sha256"]
            ):
                raise InvalidInput("skill_export_source_changed")
            if value["bytes"] + sum(len(raw) for raw in files.values()) > MAX_BYTES:
                raise InvalidInput("skill_export_capacity")
            for name, raw in files.items():
                path = contained(root, name)
                make_directory(path.parent)
                write_private(path, raw)
                value["files"][name] = digest(raw)
                value["bytes"] += len(raw)
            value["completed"].append(next_member["module_id"])
            value["known_gaps"] = sorted(set(value["known_gaps"] + gaps))
            value.pop("error_code", None)
            save(db, row, "running")
        else:
            from .skill_export_package import finish

            finish(config, db, row)
    except (InvalidInput, IntegrityError, auth.Denied, OSError) as error:
        value["error_code"] = (
            str(error)
            if isinstance(error, (InvalidInput, IntegrityError))
            else "skill_export_permissions_changed"
            if isinstance(error, auth.Denied)
            else "skill_export_storage_unavailable"
        )
        save(db, row, "failed")
    return view(row)
