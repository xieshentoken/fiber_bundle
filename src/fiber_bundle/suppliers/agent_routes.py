"""Connection-scoped read-only supplier tools; recheck enabled scope on every request."""

from typing import Any

from fastapi import APIRouter, Request

from ..agent_connections.routes import owned
from ..agent_connections.visibility import scope
from ..config import Config
from ..query_http import access
from . import query, read
from .contracts import Compare, Condition, Evidence


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/v1/agent-knowledge/suppliers")

    @routes.post("/query")
    def select(data: Condition, request: Request) -> dict[str, Any]:
        with access(config, request) as (db, client):
            assert client is not None
            owned(db, client)
            scope(config, db, client, [data.vault_id])
            return query.select(config, db, client, data)

    @routes.post("/compare")
    def compare(data: Compare, request: Request) -> dict[str, Any]:
        with access(config, request) as (db, client):
            assert client is not None
            owned(db, client)
            scope(config, db, client, [data.vault_id])
            return query.compare(config, db, client, data)

    @routes.post("/evidence")
    def evidence(data: Evidence, request: Request) -> dict[str, Any]:
        with access(config, request) as (db, client):
            assert client is not None
            owned(db, client)
            scope(config, db, client, [data.vault_id])
            return read.bounded_evidence(config, db, client, data)

    return routes
