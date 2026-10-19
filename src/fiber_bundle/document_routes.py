"""Authenticated PDF candidate transport; no parsing, promotion or model invocation."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

from fastapi import APIRouter, Path, Request
from pydantic import Field

from . import auth, database, document_evidence
from .config import Config
from .payloads import Payload


class DocumentSearch(Payload):
    query: Annotated[str, Field(min_length=1, max_length=200)]
    limit: Annotated[int, Field(ge=1, le=20)] = 10


def router(config: Config) -> APIRouter:
    routes = APIRouter()

    @contextmanager
    def access(request: Request) -> Iterator[tuple[sqlite3.Connection, str]]:
        db = database.connect(config.state_root)
        try:
            client = auth.client(db, request.headers.get("authorization", ""))
            yield db, client["id"]
        finally:
            db.close()

    @routes.get("/v1/documents/{run_id}")
    def overview(run_id: str, request: Request) -> dict[str, Any]:
        with access(request) as (db, client_id):
            return document_evidence.overview(config, db, client_id, run_id)

    @routes.get("/v1/documents/{run_id}/pages/{physical_page}")
    def page(
        run_id: str, physical_page: Annotated[int, Path(ge=1, le=100)], request: Request
    ) -> dict[str, Any]:
        with access(request) as (db, client_id):
            return document_evidence.page(config, db, client_id, run_id, physical_page)

    @routes.get("/v1/documents/{run_id}/segments/{unit_id}")
    def segment(run_id: str, unit_id: str, request: Request) -> dict[str, Any]:
        with access(request) as (db, client_id):
            return document_evidence.segment(config, db, client_id, run_id, unit_id)

    @routes.post("/v1/documents/{run_id}/search")
    def search(run_id: str, data: DocumentSearch, request: Request) -> dict[str, Any]:
        with access(request) as (db, client_id):
            return document_evidence.search(config, db, client_id, run_id, data.query, data.limit)

    return routes
