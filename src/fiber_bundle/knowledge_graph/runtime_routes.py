"""Admin-only explicit local runtime detection, outside the global storage lease."""

from typing import Any

from fastapi import APIRouter, Request

from .. import database
from ..config import Config
from ..query_http import access
from . import runtime_setup


def router(config: Config) -> APIRouter:
    routes = APIRouter()

    @routes.post("/runtime/detect")
    def detect(request: Request) -> dict[str, Any]:
        with database.locked(config), access(config, request, admin=True):
            pass
        result = runtime_setup.detect()
        with database.locked(config), access(config, request, admin=True):
            return result

    return routes
