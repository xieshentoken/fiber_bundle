"""Authenticated, short-lived control requests for the background Draft importer."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import Field, StrictBool

from ..config import Config
from ..payloads import Identifier, Payload
from ..query_http import Digest, access
from . import draft_import_tasks as tasks
from . import packages, store
from .draft_import_runtime import Runtime


class Scope(Payload):
    client_id: Identifier
    vault_id: Identifier


class Creation(Scope):
    submission_id: Identifier
    source_path: Annotated[str, Field(min_length=1, max_length=4096)]
    folder: Annotated[str, Field(max_length=160)] = ""


class Confirmation(Scope):
    preview_sha256: Digest
    moc_ids: Annotated[list[Identifier], Field(max_length=100)]
    candidate_acknowledged: StrictBool


def router(config: Config, runtime: Runtime) -> APIRouter:
    routes = APIRouter(prefix="/admin/draft-imports")

    @routes.post("", status_code=202)
    def create(data: Creation, request: Request) -> Any:
        with access(config, request, admin=True) as (db, _):
            result = tasks.create(config, db, data.model_dump())
        runtime.wake.set()
        return result

    @routes.get("")
    def listing(client_id: str, vault_id: str, request: Request) -> Any:
        with access(config, request, admin=True) as (db, _):
            packages.context(config, db, client_id, vault_id, write=True)
            rows = [
                t for t in store.rows(config, tasks.TABLE, vault_id) if t["client_id"] == client_id
            ]
            return {
                "items": [
                    tasks.public(t) for t in sorted(rows, key=lambda t: -t["created_at"])[:50]
                ]
            }

    @routes.get("/{identity}")
    def read(identity: UUID, client_id: str, vault_id: str, request: Request) -> Any:
        with access(config, request, admin=True) as (db, _):
            return tasks.public(tasks.owned(config, db, str(identity), client_id, vault_id))

    @routes.post("/{identity}/confirm", status_code=202)
    def confirm(identity: UUID, data: Confirmation, request: Request) -> Any:
        with access(config, request, admin=True) as (db, _):
            task = tasks.owned(config, db, str(identity), data.client_id, data.vault_id)
            result = tasks.confirm(config, db, task, data.model_dump())
        runtime.wake.set()
        return result

    @routes.post("/{identity}/{operation}", status_code=202)
    def action(identity: UUID, operation: str, data: Scope, request: Request) -> Any:
        with access(config, request, admin=True) as (db, _):
            task = tasks.owned(config, db, str(identity), data.client_id, data.vault_id)
            result = tasks.action(config, task, operation)
        runtime.wake.set()
        return result

    return routes
