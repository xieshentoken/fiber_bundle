"""Current scope transport for bounded graph display, title search and original note detail."""

from typing import Any

from fastapi import APIRouter, Request

from ..config import Config
from ..query_http import access
from . import projection


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/admin/knowledge-graph")

    @routes.get("/projection")
    def read(
        client_id: str, vault_id: str, request: Request, focus: str | None = None
    ) -> dict[str, Any]:
        with access(config, request, admin=True) as (main, _):
            return projection.read(config, main, client_id, vault_id, focus)

    @routes.get("/node-search")
    def search(client_id: str, vault_id: str, query: str, request: Request) -> list[dict[str, str]]:
        with access(config, request, admin=True) as (main, _):
            return projection.search(config, main, client_id, vault_id, query)

    @routes.get("/node")
    def detail(client_id: str, vault_id: str, note_id: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (main, _):
            return projection.detail(config, main, client_id, vault_id, note_id)

    return routes
