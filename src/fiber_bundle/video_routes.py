"""Authenticated video settings, upload references and explicit task controls."""

from typing import Any, Literal

from fastapi import APIRouter, Request

from . import database, video_contract, video_launch, video_links, video_tasks
from .config import Config, InvalidInput
from .payloads import Identifier, Payload
from .query_http import Digest, access


class Configure(Payload):
    settings: video_contract.Settings
    expected_sha256: Digest


class Action(Payload):
    client_id: Identifier
    operation: Literal["start", "resume", "cancel", "publish"]


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/admin/video-processing")
    from .video_skill_routes import router as skill_router

    routes.include_router(skill_router(config))

    @routes.get("/settings")
    def settings(request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return video_tasks.settings(config)

    @routes.put("/settings")
    def configure(data: Configure, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return video_tasks.configure(config, data.settings, data.expected_sha256)

    @routes.get("")
    def listing(
        client_id: Identifier, vault_id: Identifier, request: Request
    ) -> list[dict[str, Any]]:
        with access(config, request, admin=True) as (db, _):
            from . import auth, vaults

            vaults.checked_vault(config, auth.grant(db, client_id, vault_id, "query"))
            result = []
            for row in db.execute(
                "SELECT id FROM video_tasks WHERE client_id=? AND vault_id=? "
                "ORDER BY created_at DESC LIMIT 100",
                (client_id, vault_id),
            ):
                try:
                    result.append(
                        video_tasks.summary(video_tasks.get(config, db, client_id, row[0]))
                    )
                except (auth.Denied, InvalidInput):
                    result.append(
                        {
                            "id": row[0],
                            "client_id": client_id,
                            "vault_id": vault_id,
                            "state": "unavailable",
                            "reason": "video_processing_policy_changed",
                        }
                    )
            for saved in db.execute(
                "SELECT id FROM vault_operations WHERE vault_id=? AND kind='video_link' "
                "ORDER BY updated_at DESC LIMIT 100",
                (vault_id,),
            ):
                try:
                    result.append(
                        video_links.wire(video_links.get(config, db, client_id, saved[0]))
                    )
                except (auth.Denied, InvalidInput):
                    continue
            return result

    @routes.post("", status_code=201)
    def create(data: video_contract.Create, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _), database.transaction(db):
            return video_tasks.create(config, db, data)

    @routes.post("/links", status_code=202)
    def link(data: video_links.Create, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            item = video_links.create(config, db, data)
            return video_launch.launch_link(config, db, data.client_id, item["id"])

    @routes.post("/links/{identity}/actions")
    def link_action(identity: Identifier, data: Action, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            row = video_links.get(config, db, data.client_id, identity)
            if data.operation == "cancel":
                video_links.update(db, row, "cancelled")
                return video_links.wire(video_links.get(config, db, data.client_id, identity))
            if data.operation == "resume" and row["state"] in {
                "failed",
                "interrupted",
                "cancelled",
                "running",
            }:
                return video_launch.launch_link(config, db, data.client_id, identity)
            raise InvalidInput("video_action_invalid")

    @routes.post("/{identity}/actions")
    def action(identity: Identifier, data: Action, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            if data.operation != "start":
                result = video_tasks.action(config, db, data.client_id, identity, data.operation)
                if data.operation != "resume":
                    return result
            return video_launch.launch(config, db, data.client_id, identity)

    return routes
