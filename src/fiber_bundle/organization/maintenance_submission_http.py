"""Prepare a maintenance POST without the writer; commit and deliver under a short lease."""

import re
import sqlite3
import time
from typing import Any

import anyio
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .. import auth, database
from ..config import IntegrityError, InvalidInput, StorageError
from ..note_read_session import reuse
from . import maintenance, packages, store
from .maintenance_submission import commit, prepare


class SubmissionResponse(Response):
    def __init__(self, config: Any, limiter: Any, body: bytes) -> None:
        super().__init__()
        self.config, self.limiter, self.payload = config, limiter, body

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        await respond(
            self.config,
            self.limiter,
            scope,
            self.payload,
            receive,
            send,
            scope.get("fiber_submission_timing", {}),
        )


async def respond(
    config: Any, limiter: Any, scope: Any, body: bytes, receive: Any, send: Any, timing: Any
) -> None:
    from .maintenance_routes import Submission

    request = Request(scope)

    def handle() -> None:
        created = False
        task_id = None
        job = None
        phase = "prepare"
        delivering = False
        try:
            data = Submission.model_validate_json(body)
            with database.connect(config.state_root, readonly=True) as db, reuse():
                auth.admin(
                    db,
                    request.cookies.get(auth.COOKIE, ""),
                    request.headers.get("x-csrf-token", ""),
                )
                timing["submission_phase"] = "prepare"
                job = prepare(
                    config,
                    db,
                    data.client_id,
                    data.vault_id,
                    data.mode,
                    [m.model_dump() for m in data.new_mocs],
                    unavailable_action=data.unavailable_action,
                )
                started = time.monotonic()
                with (
                    database.lock_wait(1),
                    database.locked(config),
                    database.connect(config.state_root) as writer,
                ):
                    timing["storage_ms"] = round((time.monotonic() - started) * 1000, 1)
                    phase = timing["submission_phase"] = "commit"
                    auth.admin(
                        writer,
                        request.cookies.get(auth.COOKIE, ""),
                        request.headers.get("x-csrf-token", ""),
                    )
                    result = commit(config, writer, job, verify=True)
                    created = result["id"] == job["id"]
                    task_id = result["id"]
                    value = maintenance.public(
                        result, config=config, db=writer, validate_preview=False
                    )
                    value["task_created"] = created
                    auth.admin(writer, request.cookies.get(auth.COOKIE, ""))
                    timing["submission_commit_ms"] = round((time.monotonic() - started) * 1000, 1)
                    timing["stage"] = "maintenance_submitted"
                    response_message = JSONResponse(value)
                    delivering = True
                    anyio.from_thread.run(response_message, scope, receive, send)
        except auth.Denied as error:
            # Revoked/expired callers receive no vault or admitted-task metadata.
            anyio.from_thread.run(
                JSONResponse({"error": "request_denied"}, error.status), scope, receive, send
            )
        except ValidationError:
            anyio.from_thread.run(
                JSONResponse({"error": "invalid_request_body", "task_created": False}, 422),
                scope,
                receive,
                send,
            )
        except Exception as error:
            if delivering:
                raise
            timing["submission_error_type"] = type(error).__name__
            error_delivering = False
            try:
                with (
                    database.lock_wait(0.3),
                    database.locked(config),
                    database.connect(config.state_root) as gate,
                ):
                    auth.admin(
                        gate,
                        request.cookies.get(auth.COOKIE, ""),
                        request.headers.get("x-csrf-token", ""),
                    )
                    _, allowed = packages.context(
                        config, gate, data.client_id, data.vault_id, write=True
                    )
                    # A sidecar commit can succeed before response projection (or an
                    # event hook) fails. Recover its identifier without repeating POST.
                    if task_id is None and job is not None:
                        persisted = store.get(config, "jobs", job["id"])
                        if persisted is not None:
                            task_id, created = persisted["id"], True
                    details = {"task_created": created}
                    if task_id is not None:
                        details["task_id"] = task_id
                    from .read_errors import response

                    specific = response(error)
                    busy = (
                        isinstance(error, database.LockTimeout)
                        or isinstance(error, sqlite3.OperationalError)
                        and any(w in str(error).lower() for w in ("locked", "busy"))
                    )
                    payload: dict[str, Any]
                    if task_id is not None:
                        status, payload = (
                            503,
                            {"error": "maintenance_submission_result_unavailable"},
                        )
                    elif specific is not None:
                        status, payload = specific
                    elif busy:
                        status, payload = 503, {"error": "maintenance_submission_not_started"}
                    elif phase == "commit" and isinstance(error, IntegrityError):
                        status, payload = 409, {"error": "maintenance_submission_changed"}
                    elif phase == "prepare" and re.fullmatch(
                        r"(?:placement|layout)_[a-z_]{1,80}", str(error)
                    ):
                        status, payload = 409, {"error": str(error)}
                    elif isinstance(error, InvalidInput):
                        status, payload = 422, {"error": "invalid_input"}
                    else:
                        status, payload = 503, {"error": "storage_unavailable"}
                    timing["stage"] = payload["error"]
                    if any(
                        issue.get("path") and not allowed.allows(issue["path"])
                        for issue in payload.get("issues", [])
                    ):
                        raise auth.Denied(404)
                    auth.admin(gate, request.cookies.get(auth.COOKIE, ""))
                    error_message = JSONResponse({**payload, **details}, status)
                    error_delivering = True
                    anyio.from_thread.run(error_message, scope, receive, send)
            except auth.Denied as denied:
                anyio.from_thread.run(
                    JSONResponse({"error": "request_denied"}, denied.status), scope, receive, send
                )
            except database.LockTimeout:
                if error_delivering:
                    raise
                code = (
                    "maintenance_submission_not_started"
                    if phase == "prepare"
                    else "maintenance_submission_status_unconfirmed"
                )
                busy_message = JSONResponse({"error": code}, 503)
                anyio.from_thread.run(busy_message, scope, receive, send)
            except (StorageError, OSError, sqlite3.Error):
                if error_delivering:
                    raise
                # An unavailable authorization/delivery lease may expose no task
                # identity, issues, or claim that persistence did not happen.
                anyio.from_thread.run(
                    JSONResponse({"error": "storage_unavailable"}, 503), scope, receive, send
                )

    acquired = False
    try:
        try:
            with anyio.fail_after(0.3):
                await limiter.acquire()
            acquired = True
        except TimeoutError:
            await JSONResponse(
                {"error": "maintenance_submission_not_started", "task_created": False}, 503
            )(scope, receive, send)
            return
        await anyio.to_thread.run_sync(handle)
    finally:
        if acquired:
            limiter.release()
