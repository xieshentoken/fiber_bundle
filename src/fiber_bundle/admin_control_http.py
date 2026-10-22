"""Ledger progress and pause intent under a short SQLite authorization reservation.

SQLite read leases (writer reservations in WAL) span response delivery. Revocations
cannot commit between authorization and delivery, including in WAL mode. This
reservation never takes the long-running filesystem writer lease or HTTP queue.
Only pause intent and idle-task pause CAS are permitted; no note writes or retries.
"""

import json
import sqlite3
import time
from typing import Any
from uuid import UUID

import anyio
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import auth, database, vaults
from .config import IntegrityError, InvalidInput, StorageError
from .desktop_routes import snapshot as backend_snapshot
from .organization import execution_control, maintenance, overview, packages, store
from .organization.maintenance_routes import Action
from .organization.routes import Scope


async def respond(
    config: Any,
    limiter: Any,
    scope: Any,
    body: bytes,
    receive: Any,
    send: Any,
    timing: Any,
    lifecycle: Any = None,
) -> None:
    request = Request(scope)
    busy_code = (
        "organization_control_busy"
        if scope["method"] == "POST"
        else "organization_progress_busy"
        if scope["path"] == "/admin/organization/progress"
        else "storage_busy"
    )

    def handle() -> None:
        started = time.monotonic()
        delivering = False
        try:
            with database.lock_wait(0.3), database.connect(config.state_root) as db:
                # Rollback-journal readers hold a shared commit barrier. WAL needs
                # a writer reservation because WAL snapshots do not block revocation.
                mode = db.execute("PRAGMA journal_mode").fetchone()[0]
                db.execute("BEGIN IMMEDIATE" if mode == "wal" else "BEGIN")
                timing["storage_ms"] = round((time.monotonic() - started) * 1000, 1)
                try:
                    mutation = scope["method"] == "POST"
                    auth.admin(
                        db,
                        request.cookies.get(auth.COOKIE, ""),
                        request.headers.get("x-csrf-token", "") if mutation else None,
                    )
                    if mutation:
                        data = Action.model_validate_json(body)
                        identity = scope["path"].split("/")[-2]
                        UUID(identity)
                        packages.context(config, db, data.client_id, data.vault_id, write=True)
                        job = store.get(config, "jobs", identity)
                        if (
                            not job
                            or not job.get("maintenance")
                            or (job["client_id"], job["vault_id"])
                            != (data.client_id, data.vault_id)
                        ):
                            raise auth.Denied(404)
                        auth.admin(
                            db,
                            request.cookies.get(auth.COOKIE, ""),
                            request.headers.get("x-csrf-token", ""),
                        )
                        if job["state"] in {
                            "queued",
                            "running",
                            "cancelling",
                            "preview",
                            "partial",
                            "blocked",
                        }:
                            job = execution_control.pause(config, job)
                        value = maintenance.public(job, config=config)
                    elif scope["path"] == "/admin/backend":
                        value = backend_snapshot(config, lifecycle)
                    elif scope["path"] == "/admin/vaults":
                        value = vaults.list_vaults(db)
                    elif scope["path"] == "/admin/clients":
                        value = auth.list_clients(db)
                    elif scope["path"] == "/admin/session":
                        csrf, expires = auth.resume_session(
                            db, request.cookies.get(auth.COOKIE, "")
                        )
                        value = {"csrf": csrf, "expires_at": expires}
                    else:
                        selected = Scope.model_validate(
                            {
                                key: request.query_params.get(key)
                                for key in ("client_id", "vault_id")
                            }
                        )
                        value = overview.progress(config, db, selected.client_id, selected.vault_id)
                    # Expiry is checked again after filesystem policy checks/ledger reads.
                    auth.admin(db, request.cookies.get(auth.COOKIE, ""))
                    timing["stage"] = "control_delivery"
                    delivering = True
                    anyio.from_thread.run(JSONResponse(value), scope, receive, send)
                finally:
                    db.rollback()
        except auth.Denied as exc:
            anyio.from_thread.run(
                JSONResponse({"error": "request_denied"}, exc.status), scope, receive, send
            )
        except (ValidationError, ValueError, json.JSONDecodeError):
            anyio.from_thread.run(
                JSONResponse({"error": "invalid_request_body"}, 422), scope, receive, send
            )
        except sqlite3.OperationalError as exc:
            if not any(word in str(exc).lower() for word in ("locked", "busy")):
                raise
            timing["storage_ms"] = round((time.monotonic() - started) * 1000, 1)
            timing["stage"] = "control_metadata_busy"
            anyio.from_thread.run(
                JSONResponse({"error": busy_code}, 503, headers={"retry-after": "1"}),
                scope,
                receive,
                send,
            )
        except (StorageError, OSError) as exc:
            if delivering:
                raise
            code = "invalid_input" if isinstance(exc, InvalidInput) else "storage_unavailable"
            if isinstance(exc, IntegrityError):
                code = "storage_unavailable"
            anyio.from_thread.run(
                JSONResponse({"error": code}, 422 if code == "invalid_input" else 503),
                scope,
                receive,
                send,
            )

    started = time.monotonic()
    try:
        with anyio.fail_after(0.3):
            await limiter.acquire()
    except TimeoutError:
        timing["stage"] = "control_capacity"
        await JSONResponse({"error": busy_code}, 503, headers={"retry-after": "1"})(
            scope, receive, send
        )
        return
    timing["queue_ms"] = round((time.monotonic() - started) * 1000, 1)
    try:
        await anyio.to_thread.run_sync(handle)
    finally:
        limiter.release()
