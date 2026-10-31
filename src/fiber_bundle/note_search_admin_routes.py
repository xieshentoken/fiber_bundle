"""Administrator-session note search as an explicitly selected client identity."""

from typing import Annotated, Any

from fastapi import APIRouter, Request
from pydantic import Field

from . import auth, search
from .config import Config
from .payloads import Identifier, Payload
from .query_http import access, active


class NoteSearch(Payload):
    client_id: Identifier
    query: Annotated[str, Field(min_length=1, max_length=200)]
    limit: Annotated[int, Field(strict=True, ge=1, le=20)] = 20


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/admin")

    @routes.post("/note-search")
    def retrieve(data: NoteSearch, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            try:
                active(db, data.client_id)
            except auth.Denied as exc:
                # A withdrawn search identity does not invalidate the administrator session.
                raise auth.Denied(404) from exc
            return search.search(config, db, data.client_id, data.query, None, data.limit)

    return routes
