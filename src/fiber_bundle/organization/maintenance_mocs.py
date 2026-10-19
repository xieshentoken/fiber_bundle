"""Create only previewed MOCs, then adopt them through the existing MOC journal."""

import os
from typing import Any

from .. import moc_registration, vault_directories
from ..config import IntegrityError
from ..objects import digest
from ..publication_file_io import read
from . import packages, store


def create(config: Any, db: Any, job: Any) -> None:
    root, allowed = packages.context(config, db, job["client_id"], job["vault_id"], write=True)
    for moc in job["new_mocs"]:
        if moc.get("adopted"):
            continue
        if not allowed.allows(moc["path"]):
            raise IntegrityError("maintenance_moc_scope_changed")
        parent, name = moc["path"].rsplit("/", 1)
        with vault_directories.opened_parent(root, allowed, parent) as (_, fd):
            if not moc.get("claim"):
                handle = os.open(
                    name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=fd
                )
                try:
                    info = os.fstat(handle)
                    moc["claim"] = [info.st_dev, info.st_ino]
                    store.put(config, "jobs", job["id"], job["vault_id"], job)
                    with os.fdopen(handle, "wb", closefd=False) as f:
                        f.write(moc["text"].encode())
                        f.flush()
                        os.fsync(handle)
                    os.fsync(fd)
                finally:
                    os.close(handle)
            if not moc.get("registration"):
                raw, key = read(root, moc["path"])
                if key[:2] != moc["claim"] or digest(raw) != moc["sha256"]:
                    raise IntegrityError("maintenance_new_moc_changed")
                packet = moc_registration.preview(
                    config, db, job["client_id"], job["vault_id"], moc["path"], moc["sha256"]
                )
                moc["registration"] = packet["operation_id"]
                store.put(config, "jobs", job["id"], job["vault_id"], job)
            started = moc.get("adoption_started", False)
            moc["adoption_started"] = True
            store.put(config, "jobs", job["id"], job["vault_id"], job)
            moc_registration.apply(
                config,
                db,
                job["client_id"],
                job["vault_id"],
                moc["registration"],
                "reconcile" if started else "apply",
            )
            moc["adopted"] = True
            store.put(config, "jobs", job["id"], job["vault_id"], job)
