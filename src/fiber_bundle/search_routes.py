"""Thin authenticated search transport. No indexing or ranking in the route."""

from typing import Annotated, Any

from fastapi import APIRouter, Request
from pydantic import Field

from . import auth, database, search
from .config import Config
from .payloads import Identifier, Payload


class SearchRequest(Payload):
    query: Annotated[str, Field(min_length=1, max_length=200)]
    vault_ids: Annotated[list[Identifier], Field(max_length=20)] | None = None
    limit: Annotated[int, Field(ge=1, le=20)] = 10
    session_id: Identifier | None = None


def router(config: Config) -> APIRouter:
    routes = APIRouter()

    @routes.post("/v1/search")
    def retrieve(data: SearchRequest, request: Request) -> dict[str, Any]:
        db = database.connect(config.state_root)
        try:
            client = auth.client(db, request.headers.get("authorization", ""))
            return search.search(
                config,
                db,
                client["id"],
                data.query,
                data.vault_ids,
                data.limit,
                session_id=data.session_id,
            )
        finally:
            db.close()

    return routes
