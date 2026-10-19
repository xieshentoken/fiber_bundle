"""Explicit local index refresh; body work runs outside the HTTP/global storage lease."""

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request

from . import auth, database, note_search_build, note_search_cache, note_search_jobs
from .config import Config
from .payloads import Identifier, Payload
from .query_http import access


class Scope(Payload):
    client_id: Identifier
    vault_id: Identifier


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/admin/note-index")

    @routes.get("/summary")
    def summary(
        request: Request,
        offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ) -> dict[str, Any]:
        from .note_search_summary import listing

        with access(config, request, admin=True) as (db, _):
            return listing(config, db, offset, limit)

    @routes.get("/status")
    def status(client_id: str, vault_id: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            vault = auth.grant(db, client_id, vault_id, "query")
            return {
                **note_search_cache.status(config, db, vault),
                "update": note_search_jobs.read(config, vault_id),
            }

    @routes.post("/update")
    def update(data: Scope, request: Request) -> dict[str, Any]:
        with database.locked(config), access(config, request, admin=True) as (db, _):
            auth.grant(db, data.client_id, data.vault_id, "query")
        result = note_search_build.build(config, data.client_id, data.vault_id)
        with database.locked(config), access(config, request, admin=True) as (db, _):
            vault = auth.grant(db, data.client_id, data.vault_id, "query")
            if note_search_cache.status(config, db, vault)["state"] != "ready":
                raise note_search_cache.IndexUnavailable("note_index_update_required")
            return result

    return routes
