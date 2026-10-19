"""Explicit persistent import/index jobs; cancellation is checked before every write."""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import (
    auth,
    database,
    vault_catalog,
    vault_scope,
)
from .config import Config, InvalidInput
from .inputs import canonical


class Cancelled(Exception):
    pass


def row(db: sqlite3.Connection, jid: str) -> sqlite3.Row:
    from .note_changes import identifier

    identifier(jid)
    value = db.execute("SELECT * FROM vault_operations WHERE id=?", (jid,)).fetchone()
    if not isinstance(value, sqlite3.Row):
        raise auth.Denied(404)
    return value


def view(value: sqlite3.Row) -> dict[str, Any]:
    data = json.loads(value["payload"])
    return {
        k: value[k] for k in ("id", "vault_id", "kind", "state", "created_at", "updated_at")
    } | {
        k: data[k]
        for k in (
            "completed",
            "total",
            "destination",
            "error_code",
            "result",
            "phase",
            "partial_directory_retained",
            "root",
        )
        if k in data
    }


def destination(config: Config, db: sqlite3.Connection, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or path == Path(path.anchor):
        raise InvalidInput("vault_destination_invalid")
    vault_scope.effective(path, {"include": [""], "exclude": []})
    if (
        path.exists()
        or path.is_symlink()
        or not path.parent.is_dir()
        or path.parent.resolve(strict=True) != path.parent
    ):
        raise InvalidInput("vault_destination_must_be_fresh")
    state = config.state_root
    if path == state or state in path.parents or path in state.parents:
        raise InvalidInput("vault_root_overlaps_state")
    vault_catalog.overlaps(config, db, path)
    return path


def creation_preview(
    config: Config, db: sqlite3.Connection, name: str, target: str
) -> dict[str, Any]:
    from .inputs import text

    text(name, limit=100)
    path = destination(config, db, target)
    if db.execute("SELECT 1 FROM vaults WHERE name=?", (name,)).fetchone():
        raise InvalidInput("vault_name_exists")
    info = path.parent.stat()
    value = {
        "mode": "create",
        "name": name,
        "destination": str(path),
        "parent_key": [info.st_dev, info.st_ino],
    }
    identity = str(uuid4())
    db.execute(
        "INSERT INTO vault_admissions(id,payload,expires_at) VALUES(?,?,?)",
        (identity, canonical(value), time.time() + 600),
    )
    return {
        "preview_id": identity,
        **value,
        "notes": 0,
        "attachments": 0,
        "bytes": 0,
        "model_calls": 0,
    }


def prepare(
    config: Config,
    db: sqlite3.Connection,
    *,
    mode: str,
    preview_id: str | None,
    name: str,
    target: str | None,
    client_id: str,
    query: bool,
    original: bool,
    add: bool,
) -> dict[str, Any]:
    if mode not in {"attach", "copy", "create"}:
        raise InvalidInput("vault_mode_invalid")
    client = db.execute("SELECT 1 FROM clients WHERE id=? AND active=1", (client_id,)).fetchone()
    if client is None or not query:
        raise InvalidInput("vault_index_requires_query_identity")
    admission = None
    if mode in {"attach", "copy", "create"}:
        a = db.execute(
            "SELECT * FROM vault_admissions WHERE id=? AND consumed=0 AND expires_at>?",
            (preview_id, time.time()),
        ).fetchone()
        if a is None:
            raise InvalidInput("vault_preview_expired")
        admission = json.loads(a["payload"])
        if mode == "create":
            if (
                admission.get("mode") != "create"
                or admission["name"] != name
                or admission["destination"] != target
            ):
                raise InvalidInput("vault_preview_changed")
        elif admission.get("mode") == "create":
            raise InvalidInput("vault_preview_changed")
        if mode == "attach" and admission["read_only"] and add:
            raise InvalidInput("vault_read_only")
        name = admission["name"]
    from .inputs import text

    text(name, limit=100)
    if db.execute("SELECT 1 FROM vaults WHERE name=?", (name,)).fetchone() and not (
        mode == "attach" and admission and admission.get("existing_id")
    ):
        raise InvalidInput("vault_name_exists")
    output = None
    if mode in {"copy", "create"}:
        if target is None:
            raise InvalidInput("vault_destination_required")
        output = destination(config, db, target)
        if admission and mode == "copy":
            source = Path(admission["root"])
            if output == source or source in output.parents or output in source.parents:
                raise InvalidInput("vault_destination_overlaps_source")
    jid = str(uuid4())
    now = time.time()
    payload = {
        "mode": mode,
        "preview_id": preview_id,
        "admission": admission,
        "name": name,
        "destination": str(output) if output else None,
        "client_id": client_id,
        "permissions": {"query": query, "original": original, "add": add},
        "completed": 0,
        "total": len(admission["inventory"]["files"]) if admission and mode != "create" else 0,
    }
    db.execute(
        "INSERT INTO vault_operations VALUES(?,NULL,?,?,?,?,?)",
        (jid, mode, "prepared", canonical(payload), now, now),
    )
    return view(row(db, jid))


def cancel(db: sqlite3.Connection, jid: str) -> dict[str, Any]:
    job = row(db, jid)
    if job["kind"] not in {"attach", "copy", "create"}:
        raise InvalidInput("vault_job_invalid")
    db.execute(
        "UPDATE vault_operations SET state='cancelled',updated_at=? "
        "WHERE id=? AND state IN ('prepared','launch_requested','running')",
        (time.time(), jid),
    )
    return view(row(db, jid))


def checkpoint(config: Config, jid: str, **updates: Any) -> dict[str, Any]:
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            job = row(db, jid)
            if job["state"] != "running":
                raise Cancelled()
            data: dict[str, Any] = json.loads(job["payload"])
            data.update(updates)
            db.execute(
                "UPDATE vault_operations SET payload=?,updated_at=? WHERE id=?",
                (canonical(data), time.time(), jid),
            )
            return data
        finally:
            db.close()
