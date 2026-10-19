"""Cookie/CSRF-protected preparation of manual backup and restore intents."""

from typing import Literal

from fastapi import APIRouter, Request

from . import backup_requests
from .config import Config
from .payloads import Identifier, Payload
from .query_http import access


class Selection(Payload):
    action: Literal["create", "restore"]
    bundle: Identifier | None = None


def router(config: Config) -> APIRouter:
    routes = APIRouter()

    @routes.get("/admin/backups")
    def listing(request: Request) -> list[dict[str, object]]:
        with access(config, request, admin=True):
            return backup_requests.list_requests(config)

    @routes.post("/admin/backups", status_code=201)
    def prepare(data: Selection, request: Request) -> dict[str, object]:
        with access(config, request, admin=True):
            return backup_requests.prepare(config, data.action, data.bundle)

    return routes
