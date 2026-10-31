"""Durable unverified inbound references attached to the existing archive journal."""

import json
import stat
from typing import Any

from .. import note_identity_store, partial_notes
from ..config import IntegrityError, contained
from ..inputs import canonical
from ..objects import digest
from . import packages


def file_key(info: Any) -> list[Any]:
    return [info.st_dev, info.st_ino, float(getattr(info, "st_birthtime", 0)).hex()]


def attach(config: Any, db: Any, job: Any, value: Any) -> None:
    omissions = partial_notes.for_job(job)
    if not omissions:
        return
    if job["fixed"]["unavailable_action"] != "defer_links":
        raise IntegrityError("organization_archive_scope_incomplete")
    root, scope = packages.context(config, db, job["client_id"], job["vault_id"], write=True)
    registered = {
        r.relative_path: r.identity for r in note_identity_store.records(config, job["vault_id"])
    }
    sources = []
    for path in sorted(omissions):
        if not scope.allows(path):
            raise IntegrityError("maintenance_archive_scope_changed")
        info = contained(root, path).lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise IntegrityError("partial_note_scope_changed")
        sources.append({"path": path, "file_key": file_key(info), "identity": registered.get(path)})
    value["read_omissions"] = omissions
    value["deferred_links"] = {
        "job": job["id"],
        "state": "pending",
        "sources": sources,
        "checked": [],
    }


def records(db: Any, job: Any) -> list[Any]:
    result = []
    for item in job["items"]:
        oid = item.get("archive_operation")
        if not oid:
            continue
        row = db.execute(
            "SELECT * FROM vault_operations WHERE id=? AND vault_id=?", (oid, job["vault_id"])
        ).fetchone()
        if row is None or row["state"] != "completed":
            continue
        value = json.loads(row["payload"])
        deferred = value.get("deferred_links")
        if deferred and deferred["job"] == job["id"] and deferred["state"] == "pending":
            result.append((oid, value))
    return result


def binding(rows: Any) -> str:
    return digest(
        canonical([[oid, value["moves"], value["deferred_links"]] for oid, value in rows]).encode()
    )
