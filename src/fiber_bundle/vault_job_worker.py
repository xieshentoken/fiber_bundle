"""Run one frozen import and index admission; partial files remain privately reviewable."""

import json
import os
import time
from uuid import uuid4

from . import (
    auth,
    database,
    note_identity_store,
    note_index,
    note_inventory,
    note_search_build,
    vault_catalog,
    vault_scope,
    vaults,
)
from .config import Config, IntegrityError, contained, make_directory, sync_directory
from .inputs import canonical
from .vault_jobs import Cancelled, checkpoint, destination, row


def register(config: Config, jid: str, vid: str, cid: str) -> None:
    db = database.connect(config.state_root)
    try:
        vault = auth.grant(db, cid, vid, "query")
        inventory = note_inventory.read(config, db, vault)
    finally:
        db.close()
    info = inventory.root.stat()
    key = (info.st_dev, info.st_ino)
    records = note_identity_store.records(config, vid)
    known = {r.file_key: r for r in records}
    for i, note in enumerate(inventory.notes):
        checkpoint(config, jid, completed=i, total=len(inventory.notes), phase="registering")
        if note.file_key is None:
            raise IntegrityError("vault_note_birth_unavailable")
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                current = vaults.checked_vault(config, auth.grant(db, cid, vid, "query"))
                scope = vault_scope.for_vault(current, auth.grant(db, cid, vid, "query"))
                if not scope.allows(note.relative_path):
                    raise IntegrityError("vault_scope_changed")
                fresh = note_index.read_note(
                    current, note.relative_path, note_index.Limits().note_bytes
                )
                if (fresh.sha256, fresh.file_key) != (note.sha256, note.file_key):
                    raise IntegrityError("vault_source_changed")
                if note.file_key not in known:
                    identity = (
                        "publication:" + note.publication_id
                        if note.publication_id
                        else "note:" + str(uuid4())
                    )
                    note_identity_store.save(
                        config,
                        vid,
                        cid,
                        note_identity_store.Registration(
                            identity,
                            note.publication_id,
                            note.relative_path,
                            note.sha256,
                            note.file_key,
                            key,
                            1,
                        ),
                    )
            finally:
                db.close()


def execute(config: Config, jid: str) -> None:
    claimed = False
    created = False
    vid = None
    try:
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                if not db.execute(
                    "UPDATE vault_operations SET state='running',updated_at=? "
                    "WHERE id=? AND state='launch_requested'",
                    (time.time(), jid),
                ).rowcount:
                    return
                data = json.loads(row(db, jid)["payload"])
                claimed = True
            finally:
                db.close()
        mode = data["mode"]
        admission = data["admission"]
        target = None
        if mode in {"copy", "create"}:
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    target = destination(config, db, data["destination"])
                finally:
                    db.close()
            if (
                mode == "create"
                and list((target.parent.stat().st_dev, target.parent.stat().st_ino))
                != admission["parent_key"]
            ):
                raise IntegrityError("vault_preview_changed")
            checkpoint(config, jid)
            target.mkdir(mode=0o700)
            created = True
            sync_directory(target.parent)
            if mode == "copy":
                source, scope = vault_catalog.canonical_root(
                    config, admission["root"], admission["scope"]
                )
                info = source.stat()
                if (info.st_dev, info.st_ino) != (
                    admission["directory_dev"],
                    admission["directory_ino"],
                ) or scope.wire() != admission["effective_scope"]:
                    raise IntegrityError("vault_preview_changed")
                import hashlib

                from .query_routing_files import read

                for i, file in enumerate(admission["inventory"]["files"]):
                    checkpoint(config, jid, completed=i, phase="copying")
                    if vault_scope.effective(source, admission["scope"]).wire() != admission[
                        "effective_scope"
                    ] or not scope.allows(file["path"]):
                        raise IntegrityError("vault_scope_changed")
                    payload = read(
                        contained(source, file["path"]), 64 * 1024 * 1024, allow_empty=True
                    )
                    if (
                        len(payload) != file["bytes"]
                        or hashlib.sha256(payload).hexdigest() != file["sha256"]
                    ):
                        raise IntegrityError("vault_source_changed")
                    path = contained(target, file["path"])
                    make_directory(path.parent)
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(payload)
                        stream.flush()
                        os.fsync(stream.fileno())
                # Compare the full authorized membership and policy after copying.
                from .vault_files import inventory

                if (
                    vault_scope.effective(source, admission["scope"]).wire()
                    != admission["effective_scope"]
                    or inventory(source, scope) != admission["inventory"]
                ):
                    raise IntegrityError("vault_source_changed")
        checkpoint(config, jid)
        # Hashing stays outside the storage lease so cancellation and revocation remain available.
        db = database.connect(config.state_root)
        try:
            if mode == "attach":
                validated = vault_catalog.validate_admission(config, db, data["preview_id"])
                preview_id = data["preview_id"]
            else:
                assert target is not None
                validated = vault_catalog.observe(
                    config,
                    db,
                    name=data["name"],
                    root=str(target),
                    scope={"include": [""], "exclude": []},
                    read_only=False,
                )
                preview_id = None
        finally:
            db.close()
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                if row(db, jid)["state"] != "running":
                    raise Cancelled()
                if preview_id is None:
                    preview_id = vault_catalog.persist_preview(config, db, validated)["preview_id"]
                vid = vault_catalog.attach(config, db, preview_id, validated=validated)["vault_id"]
                if mode != "attach":
                    db.execute("UPDATE vaults SET root_kind='managed' WHERE id=?", (vid,))
                    if data["preview_id"]:
                        db.execute(
                            "UPDATE vault_admissions SET consumed=1 WHERE id=?",
                            (data["preview_id"],),
                        )
                auth.set_grant(db, data["client_id"], vid, **data["permissions"])
                db.execute("UPDATE vault_operations SET vault_id=? WHERE id=?", (vid, jid))
            finally:
                db.close()
        register(config, jid, vid, data["client_id"])
        checkpoint(config, jid, phase="indexing")
        result = note_search_build.build(
            config,
            data["client_id"],
            vid,
            cancelled=lambda main: row(main, jid)["state"] != "running",
        )
        checkpoint(config, jid, result=result, completed=result["notes"], total=result["notes"])
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                db.execute(
                    "UPDATE vault_operations SET state='complete',updated_at=? "
                    "WHERE id=? AND state='running'",
                    (time.time(), jid),
                )
            finally:
                db.close()
    except BaseException as exc:
        if not claimed:
            raise
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                if vid is not None:
                    vault_catalog.unlink(config, db, vid, keep_operation=jid)
                job = row(db, jid)
                payload = json.loads(job["payload"])
                payload["error_code"] = (
                    "cancelled" if isinstance(exc, Cancelled) else "vault_job_failed"
                )
                payload["partial_directory_retained"] = created
                db.execute(
                    "UPDATE vault_operations SET state=?,payload=?,updated_at=? WHERE id=?",
                    (
                        "cancelled"
                        if isinstance(exc, Cancelled) or job["state"] == "cancelled"
                        else "failed",
                        canonical(payload),
                        time.time(),
                        jid,
                    ),
                )
            finally:
                db.close()
        if not isinstance(exc, Cancelled):
            raise
