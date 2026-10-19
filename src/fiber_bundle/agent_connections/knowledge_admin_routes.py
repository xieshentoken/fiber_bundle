"""GUI previews use the selected connection's actual grants, never its import owner."""

from typing import Any

from fastapi import APIRouter, Request

from .. import database, published_expert
from ..config import Config
from ..query_http import access
from . import knowledge, store
from .knowledge_routes import Catalog, Expert


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/admin/agent-connections")

    @routes.post("/{identity}/knowledge-catalog")
    def catalog(identity: str, data: Catalog, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            row = store.get(db, identity)
            return knowledge.catalog(
                config, db, row["client_id"], data.vault_ids, data.offset, data.limit
            )

    @routes.post("/{identity}/knowledge-evidence")
    def evidence(identity: str, data: knowledge.Read, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            row = store.get(db, identity)
            return knowledge.read_evidence(config, db, row["client_id"], data)

    @routes.post("/{identity}/knowledge-expert")
    def expert(identity: str, data: Expert, request: Request) -> dict[str, Any]:
        with database.locked(config), access(config, request, admin=True) as (db, _):
            cid = store.get(db, identity)["client_id"]
        result = knowledge.query_expert(
            config, cid, data.module_id, data.expected_version, data.question, data.limit
        )
        with database.locked(config), access(config, request, admin=True) as (db, _):
            published_expert.text(
                config, db, cid, data.module_id.removeprefix("expert:"), data.expected_version
            )
            from ..expert_presentation import deliver

            return deliver(config, db, cid, result, data)

    return routes
