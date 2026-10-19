"""Admin-only access materials and connection-scoped, model-free knowledge preview."""

from typing import Annotated, Any

from fastapi import APIRouter, Request, Response
from pydantic import Field

from .. import search
from ..config import Config
from ..payloads import Payload
from ..query_http import access
from . import access_skill, store


class Preview(Payload):
    query: Annotated[str, Field(min_length=1, max_length=200)]
    limit: Annotated[int, Field(strict=True, ge=1, le=5)] = 5


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/admin/agent-connections")

    @routes.get("/{identity}/access-materials")
    def materials(identity: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            value = access_skill.material(config, db, identity)
            return {k: v for k, v in value.items() if k != "files"}

    @routes.get("/{identity}/access-skill")
    def download(identity: str, request: Request) -> Response:
        with access(config, request, admin=True) as (db, _):
            value = access_skill.package(access_skill.material(config, db, identity))
            return Response(
                value,
                media_type="application/zip",
                headers={
                    "Content-Disposition": "attachment; filename=Fiber-access-skill.zip",
                    "Cache-Control": "no-store",
                },
            )

    @routes.post("/{identity}/knowledge-preview")
    def preview(identity: str, data: Preview, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            row = store.get(db, identity)
            context = store.context(config, db, identity)
            if not context["enabled"]:
                from ..auth import Denied

                raise Denied(403)
            result = search.vault_search(
                config,
                db,
                row["client_id"],
                data.query,
                context["configuration"]["vault_ids"],
                data.limit,
            )
            return {
                **result,
                "model_called": False,
                "verification_scope": "Fiber-side scoped read; external host not observed",
            }

    return routes
