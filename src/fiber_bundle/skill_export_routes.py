"""Admin export wizard actions. Each explicit step is bounded and resumable."""

from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import Field

from . import skill_exports
from .config import Config
from .payloads import Payload
from .query_http import access


class Action(Payload):
    action: Literal["step", "cancel"]
    preview_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/admin/skill-exports")

    @routes.post("/preview")
    def preview(data: skill_exports.Preview, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return skill_exports.preview(config, db, data)

    @routes.get("")
    def history(request: Request, offset: int = 0) -> list[dict[str, Any]]:
        from .config import InvalidInput

        if not 0 <= offset <= 100000:
            raise InvalidInput("skill_export_capacity")
        with access(config, request, admin=True) as (db, _):
            return [
                skill_exports.view(skill_exports.get(db, row[0]))
                for row in db.execute(
                    "SELECT id FROM vault_operations WHERE kind='skill_export' "
                    "ORDER BY created_at DESC LIMIT 20 OFFSET ?",
                    (offset,),
                )
            ]

    @routes.get("/{identity}")
    def detail(identity: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return skill_exports.view(skill_exports.get(db, identity))

    @routes.post("/{identity}/actions")
    def action(identity: str, data: Action, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            if data.action == "step":
                return skill_exports.step(config, db, identity, data.preview_sha256)
            row = skill_exports.get(db, identity)
            if data.preview_sha256 != row["value"]["preview_sha256"]:
                from .config import InvalidInput

                raise InvalidInput("skill_export_confirmation_required")
            if row["state"] != "complete":
                skill_exports.save(db, row, "cancelled")
            return skill_exports.view(row)

    @routes.get("/{identity}/download")
    def download(identity: str, request: Request) -> Response:
        from .skill_export_package import download

        with access(config, request, admin=True) as (db, _):
            return Response(
                download(config, db, identity),
                media_type="application/zip",
                headers={
                    "Content-Disposition": "attachment; filename=Fiber-theme.zip",
                    "Cache-Control": "no-store",
                },
            )

    return routes
