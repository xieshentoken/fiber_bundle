"""Administrator-only V3 resources; reads do not probe, download or execute models."""

from typing import Any

from fastapi import APIRouter, Request

from . import video_skill
from .config import Config, InvalidInput, make_directory
from .inputs import canonical
from .payloads import Payload
from .query_http import Digest, access
from .query_policy_store import write_private


class Configure(Payload):
    settings: video_skill.Settings
    expected_sha256: Digest


def router(config: Config) -> APIRouter:
    routes = APIRouter()

    @routes.get("/skill")
    def inspect(request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            from .agent_connections.models import available

            return {**video_skill.inspect(config), "models": available(config)}

    @routes.put("/skill")
    def configure(data: Configure, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            if video_skill.inspect(config)["sha256"] != data.expected_sha256:
                raise InvalidInput("video_skill_settings_changed")
            video_skill.source_path(config, data.settings.model_dump())
            from .model_configuration.policy import resolve

            if data.settings.vision_model_configuration_id:
                resolve(config, data.settings.vision_model_configuration_id)
            if data.settings.asr_backend == "transcription-api":
                from .video_asr_api import selected_policy

                selected_policy(config, data.settings.model_dump())
            make_directory(config.state_root / "configs")
            write_private(
                config.state_root / "configs/video-skill.json",
                canonical(data.settings.model_dump()).encode(),
            )
            return video_skill.inspect(config)

    return routes
