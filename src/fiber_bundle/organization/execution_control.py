"""Durable pause intent; never overwrite a worker's in-flight job or undo journal."""

import fcntl
import json
import os
from contextlib import contextmanager
from typing import Any
from uuid import UUID, uuid4

from .. import database
from ..config import contained, make_directory, sync_directory


def path(config: Any, identity: str, suffix: str) -> Any:
    UUID(identity)
    return contained(config.state_root, "organization-execution/" + identity + suffix)


@contextmanager
def claim(config: Any, identity: str) -> Any:
    target = path(config, identity, ".lock")
    make_directory(target.parent)
    fd = os.open(target, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        os.close(fd)


def active(config: Any, identity: str) -> bool:
    if not path(config, identity, ".lock").exists():
        return False
    with claim(config, identity) as acquired:
        return not acquired


def requested(config: Any, job: Any) -> bool:
    target = path(config, job["id"], ".pause.json")
    if not target.exists():
        return False
    value = json.loads(target.read_bytes())
    return bool(value == {"job": job["id"], "generation": job.get("generation", 0)})


def request(config: Any, job: Any) -> None:
    target = path(config, job["id"], ".pause.json")
    make_directory(target.parent)
    temporary = target.with_name(str(uuid4()) + ".tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as out:
            json.dump({"job": job["id"], "generation": job.get("generation", 0)}, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, target)
        sync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)


def settle(config: Any, job: Any) -> None:
    if not requested(config, job):
        return
    if job["state"] in {"queued", "running", "cancelling", "preview", "partial", "blocked"}:
        job.update(
            paused_from=job["state"], state="paused", generation=job.get("generation", 0) + 1
        )
    # Commit the safe boundary before removing the durable request. A crash or
    # failed ledger write must leave the pause available to the next executor.
    from . import store

    store.put(config, "jobs", job["id"], job["vault_id"], job)
    target = path(config, job["id"], ".pause.json")
    target.unlink(missing_ok=True)
    sync_directory(target.parent)


def pause(config: Any, job: Any) -> Any:
    """Pause idle work by CAS; an active executor receives only durable intent."""

    request(config, job)
    try:
        # Never queue the HTTP control behind a writer. Only the immediate idle
        # transition needs this lease; durable intent above is already accepted.
        with database.lock_wait(0.001), database.locked(config):
            return _pause_idle(config, job)
    except database.LockTimeout:
        return job


def _pause_idle(config: Any, job: Any) -> Any:
    import time

    from ..inputs import canonical
    from . import store

    with claim(config, job["id"]) as acquired:
        if not acquired:
            return job
        # Planning commits re-read state/generation; application holds this claim.
        current = store.get(config, "jobs", job["id"])
        if current != job:
            return current
        if current["state"] not in {
            "queued",
            "running",
            "cancelling",
            "preview",
            "partial",
            "blocked",
        }:
            return current
        updated = {
            **current,
            "paused_from": current["state"],
            "state": "paused",
            "generation": current.get("generation", 0) + 1,
        }
        with store.connection(config, write=True) as db:
            assert db is not None
            db.execute("BEGIN IMMEDIATE")
            try:
                changed = db.execute(
                    "UPDATE jobs SET value=?,updated=? WHERE id=? AND value=?",
                    (canonical(updated), time.time(), current["id"], canonical(current)),
                ).rowcount
                db.commit()
            except BaseException:
                db.rollback()
                raise
        if changed:
            path(config, job["id"], ".pause.json").unlink(missing_ok=True)
            sync_directory(path(config, job["id"], ".pause.json").parent)
            from ..backup_automation.events import organization

            organization(config, {updated["id"]: updated})
            return updated
        return store.get(config, "jobs", job["id"])


def reconcile(config: Any) -> None:
    """Settle accepted idle/planning pauses under the original writer discipline."""
    from . import store

    pending = [
        job
        for job in store.rows(config, "jobs")
        if job.get("maintenance") and requested(config, job)
    ]
    for prior in pending:
        with database.locked(config), claim(config, prior["id"]) as acquired:
            if acquired:
                current = store.get(config, "jobs", prior["id"])
                if current is not None:
                    settle(config, current)
