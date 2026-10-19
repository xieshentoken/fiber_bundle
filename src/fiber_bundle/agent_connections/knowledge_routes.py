"""Bounded knowledge APIs using the current connection identity."""

from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import Field

from .. import database
from ..config import Config
from ..payloads import Payload
from ..query_http import access
from . import knowledge


class Catalog(Payload):
    vault_ids: list[str] | None = Field(default=None, max_length=20)
    offset: int = Field(default=0, strict=True, ge=0, le=100000)
    limit: int = Field(default=20, strict=True, ge=1, le=50)


class Expert(Payload):
    module_id: str = Field(min_length=1, max_length=200)
    expected_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    question: str = Field(min_length=1, max_length=256)
    limit: int = Field(default=5, strict=True, ge=1, le=8)
    output_capability: Literal["unknown", "text", "images", "full"] = "unknown"
    output_format: Literal["json", "markdown"] = "json"


class Mocs(Payload):
    vault_id: str
    moc_id: str | None = None
    offset: int = Field(default=0, strict=True, ge=0)


def router(config: Config) -> APIRouter:
    from .routes import owned

    routes = APIRouter(prefix="/v1/agent-knowledge")

    @routes.post("/catalog")
    def catalog(data: Catalog, request: Request) -> dict[str, Any]:
        with access(config, request) as (db, cid):
            assert cid is not None
            owned(db, cid)
            return knowledge.catalog(config, db, cid, data.vault_ids, data.offset, data.limit)

    @routes.post("/evidence")
    def evidence(data: knowledge.Read, request: Request) -> dict[str, Any]:
        with access(config, request) as (db, cid):
            assert cid is not None
            owned(db, cid)
            return knowledge.read_evidence(config, db, cid, data)

    @routes.post("/expert")
    def expert(data: Expert, request: Request) -> dict[str, Any]:
        # Authentication and the final delivery check each hold the existing HTTP guard lease.
        # The isolated query process runs outside it.
        with database.locked(config), access(config, request) as (db, cid):
            assert cid is not None
            owned(db, cid)
        result = knowledge.query_expert(
            config, cid, data.module_id, data.expected_version, data.question, data.limit
        )
        with database.locked(config), access(config, request) as (db, current):
            assert current is not None
            owned(db, current)
            from ..published_expert import text

            text(config, db, current, data.module_id.removeprefix("expert:"), data.expected_version)
            from ..expert_presentation import deliver

            return deliver(config, db, current, result, data)

    @routes.post("/mocs")
    def mocs(data: Mocs, request: Request) -> dict[str, Any]:
        from ..organization.routes import browse

        with access(config, request) as (db, cid):
            assert cid is not None
            owned(db, cid)
            result = browse(
                config, db, cid, data.vault_id, data.offset, exceptions=False, moc_id=data.moc_id
            )
            return {
                "mocs": [
                    {k: v for k, v in m.items() if k not in {"members", "sha256"}}
                    for m in result["mocs"]
                ],
                "members": result["items"] if data.moc_id else [],
                "next_offset": result["next_offset"] if data.moc_id else None,
                "model_called": False,
            }

    return routes
