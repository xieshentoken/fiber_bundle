"""Strict local-admin transport for reviewed Vault admission and detachment."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request
from pydantic import Field

from . import database, vault_catalog, vault_job_launch, vault_jobs, vault_trash
from .config import Config
from .payloads import Identifier, Payload, Text
from .query_http import access


class Scope(Payload):
    include: Annotated[list[str], Field(min_length=1, max_length=100)] = [""]
    exclude: Annotated[list[str], Field(max_length=100)] = []


class Preview(Payload):
    name: Text
    root: Annotated[str, Field(min_length=1, max_length=4096)]
    read_only: bool = True
    scope: Scope = Scope()


class Confirmation(Payload):
    preview_id: Identifier


class TrashConfirm(Payload):
    confirmed_root: Annotated[str, Field(min_length=1, max_length=4096)]


class CreationPreview(Payload):
    name: Text
    destination: Annotated[str, Field(min_length=1, max_length=4096)]


class Job(Payload):
    mode: Literal["attach", "copy", "create"]
    preview_id: Identifier | None = None
    name: Text
    destination: Annotated[str, Field(min_length=1, max_length=4096)] | None = None
    client_id: Identifier
    query: bool = True
    original: bool = False
    add: bool = False


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/admin/vault-catalog")

    @routes.post("/preview")
    def preview(data: Preview, request: Request) -> dict[str, Any]:
        with database.locked(config):
            with access(config, request, admin=True):
                pass
        with access(config, request, admin=True) as (db, _):
            value = vault_catalog.observe(
                config,
                db,
                name=data.name,
                root=data.root,
                read_only=data.read_only,
                scope=data.scope.model_dump(),
            )
        with database.locked(config):
            with access(config, request, admin=True) as (db, _):
                return vault_catalog.persist_preview(config, db, value)

    @routes.post("/attach")
    def attach(data: Confirmation, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return vault_catalog.attach(config, db, data.preview_id)

    @routes.post("/{vault_id}/unlink")
    def unlink(vault_id: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return vault_catalog.unlink(config, db, vault_id)

    @routes.post("/create-preview")
    def create_preview(data: CreationPreview, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return vault_jobs.creation_preview(config, db, data.name, data.destination)

    @routes.post("/jobs", status_code=201)
    def prepare(data: Job, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return vault_jobs.prepare(
                config,
                db,
                mode=data.mode,
                preview_id=data.preview_id,
                name=data.name,
                target=data.destination,
                client_id=data.client_id,
                query=data.query,
                original=data.original,
                add=data.add,
            )

    @routes.get("/jobs")
    def jobs(request: Request) -> list[dict[str, Any]]:
        with access(config, request, admin=True) as (db, _):
            return [
                vault_jobs.view(r)
                for r in db.execute(
                    "SELECT * FROM vault_operations WHERE kind "
                    "IN ('attach','copy','create','index') "
                    "ORDER BY created_at DESC LIMIT 50"
                )
            ]

    @routes.post("/jobs/{jid}/start", status_code=202)
    def start(jid: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return vault_job_launch.launch(config, db, jid)

    @routes.post("/jobs/{jid}/cancel")
    def cancel(jid: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return vault_jobs.cancel(db, jid)

    @routes.post("/{vault_id}/trash-preview")
    def trash_preview(vault_id: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return vault_trash.preview(config, db, vault_id)

    @routes.post("/trash/{jid}/confirm")
    def trash_confirm(jid: str, data: TrashConfirm, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return vault_trash.execute(config, db, jid, confirmed_root=data.confirmed_root)

    @routes.get("/trash/{jid}/reconcile")
    def trash_reconcile(jid: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return vault_trash.reconcile(config, db, jid)

    @routes.post("/trash/{jid}/reconcile")
    def confirm_recovery(jid: str, data: TrashConfirm, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return vault_trash.confirm_recovery(config, db, jid, data.confirmed_root)

    @routes.get("/trash")
    def trash_jobs(request: Request) -> list[dict[str, Any]]:
        with access(config, request, admin=True) as (db, _):
            return [
                vault_jobs.view(r)
                for r in db.execute(
                    "SELECT * FROM vault_operations WHERE kind='trash' "
                    "ORDER BY created_at DESC LIMIT 50"
                )
            ]

    return routes
