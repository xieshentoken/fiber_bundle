"""Authenticated project Skill discovery and explicit default selection."""

from typing import Any

from fastapi import APIRouter, Request
from pydantic import Field

from . import project_skills
from .config import Config
from .payloads import Payload
from .query_http import Digest, access


class Selection(Payload):
    enabled: bool = Field(strict=True)
    image: str | None
    expected_sha256: Digest | None


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/project-skills")

    @routes.get("")
    def inspect(request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return project_skills.inspect(config)

    @routes.put("")
    def select(data: Selection, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return project_skills.save(config, data.enabled, data.image, data.expected_sha256)

    return routes
