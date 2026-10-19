"""Finite explicit analysis admissions, atomic generations, cancellation and crash quarantine."""

import fcntl
import os
import time
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .. import auth, database, note_changes
from ..config import Config, IntegrityError, InvalidInput, contained, make_directory
from ..inputs import canonical
from . import intake, policy, store, vectors, worker
from .contracts import MAX_OUTPUT_BYTES, fingerprint, read_json
from .worker_entry import FAILURES


def authorize(config: Config, cid: str, vid: str) -> None:
    note_changes.identifier(cid)
    note_changes.identifier(vid)
    db = database.connect(config.state_root)
    try:
        if not db.execute("SELECT 1 FROM clients WHERE id=? AND active=1", (cid,)).fetchone():
            raise auth.Denied()
        auth.grant(db, cid, vid, "query")
    finally:
        db.close()


def submit(
    config: Config,
    cid: str,
    vid: str,
    selections: tuple[intake.Selection, ...],
    *,
    _locked: bool = False,
) -> dict[str, Any]:
    with nullcontext() if _locked else database.locked(config):
        authorize(config, cid, vid)
        profile = policy.current(config, require_enabled=True)
        assert profile is not None
        prepared = intake.prepare(
            config, None, vid, selections, profile.engine, enabled=True, client_id=cid, _locked=True
        )
        key = fingerprint(
            {
                "request": prepared.request.idempotency_key,
                "profile": profile.digest,
                "permission_epoch": prepared.permission_epoch,
            }
        )
        with store.connection(config, write=True) as db:
            assert db is not None
            with database.transaction(db):
                row = db.execute("SELECT * FROM jobs WHERE idempotency_key=?", (key,)).fetchone()
                if row is not None:
                    return store.view(row)
                if (
                    db.execute(
                        "SELECT count(*) FROM jobs WHERE client_id=? AND vault_id=?", (cid, vid)
                    ).fetchone()[0]
                    >= store.MAX_JOBS
                ):
                    raise InvalidInput("graph_job_limit")
                db.execute(
                    "INSERT INTO "
                    "jobs(id,client_id,vault_id,idempotency_key,profile_json,"
                    "prepared_json,state,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (
                        prepared.request.task_id,
                        cid,
                        vid,
                        key,
                        canonical(asdict(profile)),
                        store.encode(prepared),
                        "queued",
                        time.time(),
                    ),
                )
                return store.view(store.job(db, prepared.request.task_id, cid, vid))


def status(
    config: Config, cid: str, vid: str, jid: str | None = None, *, _locked: bool = False
) -> list[dict[str, Any]]:
    with nullcontext() if _locked else database.locked(config):
        authorize(config, cid, vid)
        with store.connection(config) as db:
            if db is None:
                return []
            if jid is not None:
                return [store.view(store.job(db, jid, cid, vid))]
            return [
                store.view(row)
                for row in db.execute(
                    "SELECT * FROM jobs WHERE client_id=? AND vault_id=? ORDER BY created_at "
                    "DESC LIMIT ?",
                    (cid, vid, store.MAX_JOBS),
                )
            ]


def _check(
    config: Config, prepared: intake.Prepared, profile: policy.Profile, *, _locked: bool = False
) -> None:
    with nullcontext() if _locked else database.locked(config):
        current = policy.current(config, require_enabled=True)
        if current != profile:
            raise IntegrityError("graph_policy_changed")
        intake.revalidate(config, None, prepared, enabled=True, allow_rename=True, _locked=True)


def _cancelled(config: Config, cid: str, vid: str, jid: str) -> bool:
    with database.locked(config), store.connection(config) as db:
        assert db is not None
        return bool(store.job(db, jid, cid, vid)["state"] != "running")


def cache(config: Config, prepared: intake.Prepared, profile: policy.Profile) -> dict[str, Any]:
    with store.connection(config) as db:
        if db is None:
            return {}
        request = prepared.request
        row = db.execute(
            "SELECT j.* FROM generations g JOIN jobs j ON j.id=g.job_id WHERE g.client_id=? "
            "AND g.vault_id=? AND g.state=?",
            (request.client_id, request.vault_id, "ready"),
        ).fetchone()
        if row is None or row["profile_json"] != canonical(asdict(profile)):
            return {}
        old = store.decode(row["prepared_json"])
        if old.permission_epoch != prepared.permission_epoch:
            return {}
        values = read_json(row["vectors_json"].encode(), MAX_OUTPUT_BYTES)
        vectors.validate(values, old.request, complete=True)
        by_id = {n.note_id: n for n in old.request.notes}
        previous_bindings = {b.note_id: b for b in old.bindings}
        current_bindings = {b.note_id: b for b in prepared.bindings}
        return {
            n.note_id: values[n.note_id]
            for n in request.notes
            if n.note_id in values
            and by_id.get(n.note_id) == n
            and previous_bindings[n.note_id].file_key == current_bindings[n.note_id].file_key
            and previous_bindings[n.note_id].vault_key == current_bindings[n.note_id].vault_key
        }


def _lock_path(config: Config, jid: str) -> Path:
    note_changes.identifier(jid)
    return contained(config.state_root, "graph-locks/" + jid + ".lock")


def execute(config: Config, cid: str, vid: str, jid: str) -> dict[str, Any]:
    # Explicit single job. Opening a page or starting the backend cannot invoke this path.
    lock = None
    claimed = False
    try:
        with database.locked(config):
            authorize(config, cid, vid)
            policy.current(config, require_enabled=True)
            with store.connection(config, write=True) as db:
                assert db is not None
                row = store.job(db, jid, cid, vid)
                if row["state"] != "queued":
                    return store.view(row)
                if db.execute(
                    "SELECT 1 FROM jobs WHERE client_id=? AND vault_id=? AND state=?",
                    (cid, vid, "running"),
                ).fetchone():
                    raise InvalidInput("graph_scope_busy")
                prepared = store.decode(row["prepared_json"])
                profile = policy.parse(read_json(row["profile_json"].encode(), 16384))
                _check(config, prepared, profile, _locked=True)
                cached = cache(config, prepared, profile)
                make_directory(contained(config.state_root, "graph-locks"))
                lock = os.open(
                    _lock_path(config, jid), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
                )
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with database.transaction(db):
                    db.execute(
                        "UPDATE jobs SET state=? WHERE id=? AND state=?", ("running", jid, "queued")
                    )
                claimed = True
        built = worker.run_build(
            config,
            prepared,
            worker.Policy(profile.image, profile.timeout_seconds),
            cached,
            check=lambda: _check(config, prepared, profile),
            cancelled=lambda: _cancelled(config, cid, vid, jid),
        )
        with database.locked(config):
            _check(config, prepared, profile, _locked=True)
            with store.connection(config, write=True) as db:
                assert db is not None
                with database.transaction(db):
                    if store.job(db, jid, cid, vid)["state"] != "running":
                        raise InvalidInput("graph_cancelled")
                    db.execute(
                        "UPDATE jobs SET "
                        "state=?,result_json=?,vectors_json=?,embedded_count=?,finished_at=? "
                        "WHERE id=?",
                        (
                            "completed",
                            built.raw_result.decode(),
                            canonical(built.vectors),
                            built.embedded_count,
                            time.time(),
                            jid,
                        ),
                    )
                    db.execute(
                        "INSERT INTO generations VALUES(?,?,?,?) ON "
                        "CONFLICT(client_id,vault_id) DO UPDATE SET "
                        "job_id=excluded.job_id,state=excluded.state",
                        (cid, vid, jid, "ready"),
                    )
                    return store.view(store.job(db, jid, cid, vid))
    except Exception as exc:
        if claimed:
            # Never store arbitrary exception strings (SDKs may include private text/paths).
            code = (
                str(exc)
                if str(exc)
                in FAILURES
                | {
                    "graph_cancelled",
                    "graph_worker_timeout",
                    "graph_worker_output_limit",
                    "graph_policy_changed",
                }
                else "graph_analysis_failed"
            )
            with database.locked(config), store.connection(config, write=True) as db:
                assert db is not None
                with database.transaction(db):
                    db.execute(
                        "UPDATE jobs SET state=?,error_code=?,finished_at=? WHERE id=? AND state=?",
                        (
                            "cancelled" if code == "graph_cancelled" else "failed",
                            code,
                            time.time(),
                            jid,
                            "running",
                        ),
                    )
        raise
    finally:
        if lock is not None:
            os.close(lock)


def cancel(
    config: Config, cid: str, vid: str, jid: str, *, _locked: bool = False
) -> dict[str, Any]:
    with nullcontext() if _locked else database.locked(config):
        authorize(config, cid, vid)
        with store.connection(config, write=True) as db:
            assert db is not None
            store.job(db, jid, cid, vid)
            with database.transaction(db):
                db.execute(
                    "UPDATE jobs SET state=?,finished_at=? WHERE id=? AND state IN (?,?)",
                    ("cancelled", time.time(), jid, "queued", "running"),
                )
            return store.view(store.job(db, jid, cid, vid))


def quarantine(
    config: Config, cid: str, vid: str, jid: str, *, _locked: bool = False
) -> dict[str, Any]:
    """Explicit crash acknowledgement. An active OS lease prevents mistaken interruption."""
    with nullcontext() if _locked else database.locked(config):
        authorize(config, cid, vid)
        with store.connection(config, write=True) as db:
            assert db is not None
            row = store.job(db, jid, cid, vid)
            if row["state"] != "running":
                return store.view(row)
            fd = os.open(_lock_path(config, jid), os.O_RDWR | os.O_NOFOLLOW)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise InvalidInput("graph_job_still_running") from None
                profile = policy.parse(read_json(row["profile_json"].encode(), 16384))
                worker.stop_abandoned(jid, profile.image)
                with database.transaction(db):
                    db.execute(
                        "UPDATE jobs SET state=?,error_code=?,finished_at=? WHERE id=?",
                        ("interrupted", "graph_process_interrupted", time.time(), jid),
                    )
                return store.view(store.job(db, jid, cid, vid))
            finally:
                os.close(fd)
