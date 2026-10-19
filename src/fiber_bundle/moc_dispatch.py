"""Opt-in bounded dispatch of frozen, explicitly associated published-note MOC plans."""

import sqlite3
from threading import Event
from typing import Any

from . import auth, database, moc_dispatch_source, moc_file_journal, moc_files, note_changes, vaults
from . import moc_dispatch_store as store
from .config import Config, IntegrityError, InvalidInput, StorageError


def client(
    config: Config, db: sqlite3.Connection, authorization: str, vid: str, *, write: bool = True
) -> str:
    cid = auth.client(db, authorization)["id"]
    if write:
        auth.grant(db, cid, vid, "add")
    vaults.checked_vault(config, auth.grant(db, cid, vid, "query"))
    return str(cid)


def view(job: store.Job) -> dict[str, Any]:
    return {
        "job_id": job.id,
        **job.request,
        "plan_id": job.plan_id,
        "state": job.state,
        "metadata_only": True,
    }


def selected(config: Config, cid: str, vid: str, jid: str) -> store.Job:
    if not isinstance(jid, str) or not note_changes.DIGEST.fullmatch(jid):
        raise InvalidInput("invalid MOC dispatch job ID")
    job = next((j for j in store.jobs(config, cid, vid) if j.id == jid), None)
    if job is None:
        raise auth.Denied(404)
    return job


def plan_for(config: Config, job: store.Job) -> moc_file_journal.Plan:
    plan = next(
        (
            p
            for p in moc_file_journal.plans(config, job.request["vault_id"], job.request["moc_id"])
            if p.plan_id == job.plan_id
        ),
        None,
    )
    if plan is None or plan.value.get("dispatch_source") != job.request:
        raise IntegrityError("MOC dispatch plan absent or source binding changed")
    return plan


def schedule_locked(
    config: Config,
    db: sqlite3.Connection,
    cid: str,
    vid: str,
    rid: str,
    mid: str,
    *,
    revision: int,
    relative: str,
    expected_sha256: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Caller holds the storage lock and has authenticated the owner."""
    # Resolve the successful submission's durable publication, never infer by URL/hash.
    row = db.execute(
        "SELECT publication_id FROM article_publication_runs WHERE run_id=?", (rid,)
    ).fetchone()
    if row is None:
        raise InvalidInput("MOC dispatch requires an already published article run")
    request = {
        "request_id": rid if request_id is None else request_id,
        "client_id": cid,
        "vault_id": vid,
        "run_id": rid,
        "publication_id": row[0],
        "moc_id": mid,
        "revision": revision,
        "path": relative,
        "expected_sha256": expected_sha256,
    }
    jid = store.identity(request)
    moc_dispatch_source.authorize(config, db, cid, request)
    existing = next((j for j in store.jobs(config, cid, vid) if j.id == jid), None)
    if existing is not None:
        return view(existing)
    if len(store.jobs(config, cid, vid)) >= store.MAX_JOBS:
        raise IntegrityError("MOC dispatch job limit exceeded")
    planned = moc_files.plan_file(
        config, db, cid, vid, mid, revision, relative, expected_sha256, source=request
    )
    moc_dispatch_source.authorize(config, db, cid, request)
    return view(store.create(config, request, planned["plan_id"]))


def schedule(
    config: Config,
    authorization: str,
    vid: str,
    rid: str,
    mid: str,
    *,
    revision: int,
    relative: str,
    expected_sha256: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    note_changes.identifier(rid)
    note_changes.identifier(mid)
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            cid = client(config, db, authorization, vid)
            return schedule_locked(
                config,
                db,
                cid,
                vid,
                rid,
                mid,
                revision=revision,
                relative=relative,
                expected_sha256=expected_sha256,
                request_id=request_id,
            )
        finally:
            db.close()


def status(config: Config, authorization: str, vid: str, jid: str) -> dict[str, Any]:
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            return view(
                selected(config, client(config, db, authorization, vid, write=False), vid, jid)
            )
        finally:
            db.close()


def dispatch(
    config: Config,
    authorization: str,
    vid: str,
    *,
    enabled: bool = False,
    maximum: int = 0,
    stop: Event | None = None,
) -> dict[str, Any]:
    if type(enabled) is not bool or type(maximum) is not int or not 0 <= maximum <= 100:
        raise InvalidInput("MOC dispatch requires a boolean switch and limit from 0 to 100")
    result: dict[str, Any] = {"state": "disabled", "processed": 0, "results": []}
    if not enabled or maximum == 0:
        return result
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            cid = client(config, db, authorization, vid)
            for _ in range(maximum):
                if stop is not None and stop.is_set():
                    result["state"] = "interrupted"
                    return result
                job = next(
                    (
                        j
                        for j in store.jobs(config, cid, vid)
                        if j.state in {"queued", "claimed", "failed"}
                    ),
                    None,
                )
                if job is None:
                    result["state"] = "idle"
                    return result
                if job.state != "queued":
                    result.update(state="blocked", blocked_job_id=job.id)
                    return result
                job = store.transition(config, job, "claimed")
                result["processed"] += 1
                try:
                    plan = plan_for(config, job)
                    moc_dispatch_source.authorize(config, db, cid, job.request)
                    outcome = moc_files.apply_file(
                        config, db, cid, vid, job.request["moc_id"], plan.plan_id
                    )
                    from .organization.note_moc_backfill import sync_members

                    sync_members(
                        config, db, cid, vid, {"publication:" + job.request["publication_id"]}
                    )
                    job = store.transition(config, job, "applied")
                except (StorageError, auth.Denied, OSError, ValueError, sqlite3.Error):
                    # A failed outcome write may leave a claimed job and applied file;
                    # neither is replayed. The ledger transition itself may also fail.
                    saved = selected(config, cid, vid, job.id)
                    if saved.state == "claimed":
                        store.transition(config, saved, "failed")
                    elif saved.state != "applied":
                        raise IntegrityError("MOC dispatch outcome conflict") from None
                    result.update(
                        state="stopped", blocked_job_id=job.id, error_code="moc_dispatch_rejected"
                    )
                    return result
                result["results"].append(
                    {**view(job), "moc_file_write_performed": outcome["moc_file_write_performed"]}
                )
            result["state"] = "limit_reached"
            return result
        finally:
            db.close()


def reconcile(config: Config, authorization: str, vid: str, jid: str) -> dict[str, Any]:
    """Confirm or repair a known file transition; never apply an unclaimed plan."""
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            cid = client(config, db, authorization, vid)
            job = selected(config, cid, vid, jid)
            if job.state not in {"claimed", "failed"}:
                raise InvalidInput("only a claimed or failed MOC dispatch may be reconciled")
            from .organization.note_moc_sync import recover_all

            recover_all(config, db, cid, vid)
            plan = plan_for(config, job)
            if plan.status in {"planned", "not-applied"}:
                # Retiring an unapplied intent has no file or identity side effect.
                outcome = "not-applied"
            else:
                moc_dispatch_source.authorize(config, db, cid, job.request, recovery=True)
                result = moc_files.apply_file(
                    config, db, cid, vid, job.request["moc_id"], plan.plan_id, reconcile=True
                )
                outcome = result["status"]
                if outcome == "applied":
                    from .organization.note_moc_backfill import sync_members

                    sync_members(
                        config, db, cid, vid, {"publication:" + job.request["publication_id"]}
                    )
            job = store.transition(config, job, outcome)
            return {**view(job), "moc_file_write_performed": False}
        finally:
            db.close()
