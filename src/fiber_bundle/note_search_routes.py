"""Read-only index readiness for a currently authorized Vault."""

from typing import Any

from fastapi import APIRouter, Request

from . import auth, note_search_cache
from .config import Config
from .query_http import access


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/v1")

    @routes.get("/vaults/{vault_id}/note-index")
    def status(vault_id: str, request: Request) -> dict[str, Any]:
        with access(config, request) as (db, client_id):
            assert client_id is not None
            vault = auth.grant(db, client_id, vault_id, "query")
            return note_search_cache.status(config, db, vault)

    return routes
