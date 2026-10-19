"""Same-origin local administration routes; no client token can authorize them."""

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from fiber_bundle import admin_batches, auth, database, operations, system_settings, vaults
from fiber_bundle.config import Config
from fiber_bundle.http_guard import Settings
from fiber_bundle.payloads import Approval, DefaultQuery, Login, NewClient, Permissions


class BatchChoice(BaseModel):
    action: str
    question: str | None = None


def router(config: Config, settings: Settings) -> APIRouter:
    routes = APIRouter(prefix="/admin")
    failures: list[float] = []  # Single loopback listener; mutations serialized by Guard.

    @contextmanager
    def access(request: Request) -> Iterator[sqlite3.Connection]:
        db = database.connect(config.state_root)
        try:
            auth.admin(
                db,
                request.cookies.get(auth.COOKIE, ""),
                request.headers.get("x-csrf-token", "")
                if request.method not in {"GET", "HEAD"}
                else None,
            )
            yield db
        finally:
            db.close()

    @routes.post("/login")
    def login(data: Login, request: Request, response: Response) -> dict[str, Any]:
        now = time.monotonic()
        failures[:] = [stamp for stamp in failures if stamp > now - settings.login_window]
        if len(failures) >= settings.login_attempts:
            raise auth.Denied(429)
        db = database.connect(config.state_root)
        try:
            session_ttl = system_settings.inspect(db, settings.session_ttl)["session_ttl"]
            try:
                token, csrf, expires = auth.login(db, data.password, session_ttl)
            except auth.Denied:
                failures.append(now)
                raise
            failures.clear()
            old_token = request.cookies.get(auth.COOKIE, "")
            auth.end_session(db, old_token)
            response.set_cookie(
                auth.COOKIE,
                token,
                max_age=session_ttl,
                httponly=True,
                samesite="strict",
                path="/",
            )
            return {"csrf": csrf, "expires_at": expires}
        finally:
            db.close()

    @routes.get("/session")
    def session(request: Request) -> dict[str, Any]:
        with access(request) as db:
            csrf, expires = auth.resume_session(db, request.cookies.get(auth.COOKIE, ""))
            return {"csrf": csrf, "expires_at": expires}

    @routes.post("/logout")
    def logout(request: Request, response: Response) -> dict[str, bool]:
        with access(request) as db:
            auth.end_session(db, request.cookies.get(auth.COOKIE, ""))
        response.delete_cookie(auth.COOKIE, path="/", httponly=True, samesite="strict")
        return {"logged_out": True}

    @routes.get("/clients")
    def clients(request: Request) -> list[dict[str, object]]:
        with access(request) as db:
            return auth.list_clients(db)

    @routes.post("/clients", status_code=201)
    def create_client(data: NewClient, request: Request) -> dict[str, str]:
        with access(request) as db:
            return auth.create_client(db, data.name, data.create_vault)

    @routes.post("/clients/{client_id}/revoke")
    def revoke(client_id: str, request: Request) -> dict[str, bool]:
        with access(request) as db:
            auth.revoke_client(db, client_id)
            return {"revoked": True}

    @routes.put("/clients/{client_id}/vaults/{vault_id}")
    def permissions(
        client_id: str, vault_id: str, data: Permissions, request: Request
    ) -> dict[str, bool]:
        with access(request) as db:
            vaults.set_permissions(config, db, client_id, vault_id, **data.model_dump())
            return {"updated": True}

    @routes.get("/vaults")
    def list_vaults(request: Request) -> list[dict[str, object]]:
        with access(request) as db:
            return vaults.list_vaults(db)

    @routes.patch("/vaults/{vault_id}")
    def default_query(vault_id: str, data: DefaultQuery, request: Request) -> dict[str, bool]:
        with access(request) as db:
            vaults.set_default_query(db, vault_id, data.default_query)
            return {"updated": True}

    @routes.post("/vaults/{vault_id}/approve")
    def approve(vault_id: str, data: Approval, request: Request) -> dict[str, bool]:
        with access(request) as db:
            vaults.approve(
                config,
                db,
                vault_id,
                data.client_id,
                query=data.query,
                original=data.original,
                add=data.add,
            )
            return {"approved": True}

    @routes.get("/receipts/{job_id}")
    def receipt(job_id: str, request: Request) -> dict[str, Any]:
        with access(request) as db:
            return operations.receipt_status(config, db, None, job_id)

    @routes.get("/batches/pending")
    def pending_batches(request: Request) -> list[dict[str, Any]]:
        with access(request):
            return admin_batches.pending(config)

    @routes.get("/batches")
    def batch_history(request: Request) -> list[dict[str, Any]]:
        with access(request):
            return admin_batches.pending(config, include_selected=True)

    @routes.post("/batches/{batch_id}/choice")
    def choose_batch(batch_id: str, data: BatchChoice, request: Request) -> dict[str, Any]:
        with access(request) as db:
            return admin_batches.choose(config, db, batch_id, data.action, data.question)

    return routes
