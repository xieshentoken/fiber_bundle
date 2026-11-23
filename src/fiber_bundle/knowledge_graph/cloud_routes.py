"""Authenticated transport for read-only note-cloud snapshots and title search."""

from typing import Any

from fastapi import APIRouter, Query, Request

from ..config import Config
from ..query_http import access
from . import cloud


def router(config: Config) -> APIRouter:
    routes = APIRouter()

    @routes.get("/note-cloud")
    def snapshot(
        client_id: str,
        vault_id: str,
        request: Request,
        max_nodes: int = Query(default=5000, ge=1, le=cloud.MAX_NODES),
        focus: str | None = Query(default=None, max_length=100),
    ) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return cloud.read(config, db, client_id, vault_id, max_nodes=max_nodes, focus=focus)

    @routes.get("/note-cloud-search")
    def search(
        client_id: str,
        vault_id: str,
        request: Request,
        query: str = Query(min_length=1, max_length=200),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return cloud.search(config, db, client_id, vault_id, query, limit=limit)

    return routes
