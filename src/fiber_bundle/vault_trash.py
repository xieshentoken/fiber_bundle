"""Reviewed macOS Trash intents; filesystem and catalog outcomes are reconciled explicitly."""

import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import auth, database, vault_catalog
from .config import Config, IntegrityError, InvalidInput
from .inputs import canonical


def root(config: Config, row: Any) -> Path:
    path = vault_catalog.physical_path(config, row)
    path, scope = vault_catalog.canonical_root(config, str(path), json.loads(row["scope_json"]))
    info = path.stat()
    if row["directory_ino"] is not None and (info.st_dev, info.st_ino) != (
        row["directory_dev"],
        row["directory_ino"],
    ):
        raise IntegrityError("vault directory identity changed")
    if scope.exclude or scope.include != ("",):
        raise InvalidInput("vault_trash_protected_or_partial_scope")
    return path


def preview(config: Config, db: Any, vid: str) -> dict[str, Any]:
    from .vault_merge_guard import check

    check(db, vid)
    row = db.execute("SELECT * FROM vaults WHERE id=?", (vid,)).fetchone()
    if row is None:
        raise auth.Denied(404)
    if row["attachment_state"] not in {"attached", "detached"}:
        raise InvalidInput("vault_operation_requires_reconciliation")
    path = root(config, row)
    vault_catalog.overlaps(config, db, path, except_id=vid)
    from .backup_inventory import Inventory, Limits

    observed = Inventory(Limits())
    observed.tree(path, "vault")
    info = path.stat()
    now = time.time()
    jid = str(uuid4())
    value = {
        "root": str(path),
        "root_key": [info.st_dev, info.st_ino],
        "files": sum(e["kind"] == "file" for e in observed.entries),
        "bytes": observed.byte_count,
        "entries": observed.entries,
        "expires_at": now + 600,
        "synchronized": "Mobile Documents" in path.parts,
    }
    db.execute(
        "INSERT INTO vault_operations VALUES(?,?,?,?,?,?,?)",
        (jid, vid, "trash", "prepared", canonical(value), now, now),
    )
    return {
        "id": jid,
        "vault_id": vid,
        "state": "prepared",
        **{k: v for k, v in value.items() if k != "entries"},
    }


def checked_helper() -> Path:
    if sys.platform != "darwin":
        raise InvalidInput("vault_trash_macos_required")
    helper = Path(os.environ.get("FIBER_TRASH_HELPER", ""))
    expected = os.environ.get("FIBER_TRASH_HELPER_SHA256", "")
    if (
        not helper.is_absolute()
        or helper.resolve() != helper
        or not helper.is_file()
        or len(expected) != 64
        or stat.S_IMODE(helper.stat().st_mode) & 0o022
    ):
        raise InvalidInput("vault_trash_helper_required")
    if hashlib.sha256(helper.read_bytes()).hexdigest() != expected:
        raise IntegrityError("vault_trash_helper_changed")
    return helper


def move(path: Path) -> str:
    helper = checked_helper()
    result = subprocess.run(
        [str(helper), str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=True,
    )
    if len(result.stdout) > 4096:
        raise IntegrityError("vault_trash_result_invalid")
    value = json.loads(result.stdout)
    if value.get("schema") != "vault-trash/v1":
        raise IntegrityError("vault_trash_result_invalid")
    target = Path(value["trash_path"])
    if not target.is_absolute():
        raise IntegrityError("vault_trash_result_invalid")
    return str(target)


def execute(config: Config, db: Any, jid: str, *, confirmed_root: str) -> dict[str, Any]:
    from .vault_jobs import row

    job = row(db, jid)
    if job["kind"] != "trash" or job["state"] != "prepared":
        raise InvalidInput("vault_operation_requires_reconciliation")
    data = json.loads(job["payload"])
    if data["root"] != confirmed_root or data["expires_at"] <= time.time():
        raise InvalidInput("vault_preview_changed")
    from .vault_merge_guard import check

    check(db, job["vault_id"])
    vault = db.execute("SELECT * FROM vaults WHERE id=?", (job["vault_id"],)).fetchone()
    path = root(config, vault)
    if list((path.stat().st_dev, path.stat().st_ino)) != data["root_key"]:
        raise InvalidInput("vault_preview_changed")
    vault_catalog.overlaps(config, db, path, except_id=job["vault_id"])
    from .backup_inventory import Inventory, Limits

    current = Inventory(Limits())
    current.tree(path, "vault")
    if current.entries != data["entries"]:
        raise InvalidInput("vault_preview_changed")
    # Missing/drifted runtime is a known preflight failure, not an ambiguous move.
    checked_helper()
    # Intent is durable before any system move. Access closes before the filesystem step.
    if vault["attachment_state"] == "attached":
        vault_catalog.unlink(config, db, job["vault_id"])
    with database.transaction(db):
        db.execute(
            "UPDATE vault_operations SET state='running',updated_at=? WHERE id=?",
            (time.time(), jid),
        )
        db.execute(
            "UPDATE vaults SET attachment_state='trash_pending' WHERE id=?", (job["vault_id"],)
        )
    try:
        data["trash_path"] = move(path)
        if path.exists():
            raise IntegrityError("vault_trash_result_ambiguous")
    except BaseException:
        with database.transaction(db):
            db.execute(
                "UPDATE vault_operations SET state='reconcile',updated_at=? WHERE id=?",
                (time.time(), jid),
            )
            db.execute(
                "UPDATE vaults SET attachment_state='reconcile' WHERE id=?", (job["vault_id"],)
            )
        raise InvalidInput("vault_operation_requires_reconciliation") from None
    with database.transaction(db):
        db.execute(
            "UPDATE vault_operations SET state='complete',payload=?,updated_at=? WHERE id=?",
            (canonical(data), time.time(), jid),
        )
        db.execute("UPDATE vaults SET attachment_state='trashed' WHERE id=?", (job["vault_id"],))
    return {
        "id": jid,
        "vault_id": job["vault_id"],
        "state": "trashed",
        "trash_path": data["trash_path"],
        "automatic_reauthorization": False,
    }


def reconcile(config: Config, db: Any, jid: str) -> dict[str, Any]:
    from .vault_jobs import row

    job = row(db, jid)
    if job["kind"] != "trash" or job["state"] not in {"reconcile", "running"}:
        raise InvalidInput("vault_operation_requires_reconciliation")
    data = json.loads(job["payload"])
    path = Path(data["root"])
    # Only root existence and identity, no recursive read or destructive retry.
    exists = path.exists()
    same = False
    if exists and not path.is_symlink():
        info = path.stat()
        same = [info.st_dev, info.st_ino] == data["root_key"]
    return {
        "id": jid,
        "state": "reconcile",
        "original_path_exists": exists,
        "same_directory": same,
        "retry_allowed": False,
        "instruction": "请在系统废纸篓核对并恢复目录，再显式重新接入。",
    }


def confirm_recovery(config: Config, db: Any, jid: str, confirmed_root: str) -> dict[str, Any]:
    from .vault_jobs import row

    job = row(db, jid)
    data = json.loads(job["payload"])
    result = reconcile(config, db, jid)
    if confirmed_root != data["root"] or not result["same_directory"]:
        raise InvalidInput("vault_recovery_identity_unconfirmed")
    vault = db.execute("SELECT * FROM vaults WHERE id=?", (job["vault_id"],)).fetchone()
    root(config, vault)
    with database.transaction(db):
        db.execute("UPDATE vaults SET attachment_state='detached' WHERE id=?", (job["vault_id"],))
        db.execute(
            "UPDATE vault_operations SET state='reconciled',updated_at=? WHERE id=?",
            (time.time(), jid),
        )
    return {
        "id": jid,
        "vault_id": job["vault_id"],
        "state": "detached",
        "automatic_reauthorization": False,
    }
