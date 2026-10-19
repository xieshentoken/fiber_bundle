"""Owner-authenticated terminology transport for GUI clients; no automatic approval."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

from fastapi import APIRouter, Request
from pydantic import Field

from . import auth, database, media_glossary
from .config import Config, InvalidInput
from .media_glossary_contract import MAX_BYTES, MAX_REVISIONS
from .payloads import Payload


class Confirmation(Payload):
    revision: Annotated[int, Field(ge=1, le=MAX_REVISIONS, strict=True)]
    snapshot_sha256: Annotated[str, Field(pattern="^[a-f0-9]{64}$")]
    allow_empty: Annotated[bool, Field(strict=True)] = False


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/v1/media/{job_id}/glossary")

    @contextmanager
    def access(request: Request) -> Iterator[tuple[sqlite3.Connection, str]]:
        db = database.connect(config.state_root)
        try:
            client = auth.client(db, request.headers.get("authorization", ""))
            yield db, client["id"]
        finally:
            db.close()

    @routes.get("")
    def show(job_id: str, request: Request) -> dict[str, Any]:
        with access(request) as (db, client_id):
            return media_glossary.show_locked(config, db, client_id, job_id)

    @routes.post("")
    async def freeze(job_id: str, request: Request) -> dict[str, Any]:
        with access(request) as (db, client_id):
            raw = await request.body()
            if len(raw) > MAX_BYTES:
                raise InvalidInput("media_glossary_size")
            return media_glossary.freeze_locked(config, db, client_id, job_id, raw)

    @routes.post("/confirm")
    def confirm(job_id: str, data: Confirmation, request: Request) -> dict[str, Any]:
        with access(request) as (db, client_id):
            return media_glossary.confirm_locked(
                config,
                db,
                client_id,
                job_id,
                data.revision,
                data.snapshot_sha256,
                allow_empty=data.allow_empty,
            )

    return routes
