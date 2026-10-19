"""Admin/CSRF checked role control; image IO runs outside the global storage lease."""

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request
from pydantic import Field

from ... import database
from ...config import Config, InvalidInput
from ...payloads import Payload
from ...query_http import access
from .. import operations, store
from . import control, policy, runtime_image


class Change(Payload):
    expected_revision: Annotated[int, Field(strict=True, ge=0)]


class Activate(Payload):
    expected_channel_revision: Annotated[int, Field(strict=True, ge=1)]


def authorization(config: Config, request: Request, identity: str) -> Callable[[], Any]:
    def check() -> Any:
        with access(config, request, admin=True) as (main, _), store.readonly(config) as side:
            if side is None:
                raise InvalidInput("channel_service_binding_unavailable")
            channel = operations.entry(config, side, identity)
            if channel.get("origin") == "legacy":
                raise InvalidInput("channel_service_legacy_not_supported")
            store.authorize(config, main, channel)
            return channel

    return check


def router(config: Config) -> APIRouter:
    routes = APIRouter()

    @routes.post("/{identity}/services/activate")
    def activate(identity: str, data: Activate, request: Request) -> Any:
        authorize = authorization(config, request, identity)
        with database.locked(config):
            current = authorize()
            if current["revision"] != data.expected_channel_revision:
                raise InvalidInput("channel_service_revision_conflict")
            if policy.managed(config, identity):
                raise InvalidInput("channel_services_already_independent")
            available = policy.roles(current)
        image = runtime_image.pin(config)
        # Activation is one bounded handoff: freeze all role boundaries before launching.
        with database.locked(config):
            current = authorize()
            if current["revision"] != data.expected_channel_revision or policy.managed(
                config, identity
            ):
                raise InvalidInput("channel_service_revision_conflict")
            for role in available:
                policy.change(config, identity, role, 0, enabled=False)
        for role in available:
            control.start(config, identity, role, 1, authorize=authorize, image=image)
        return {role: control.status(config, identity, role) for role in available}

    @routes.post("/{identity}/services/{role}/{operation}")
    def change(
        identity: str,
        role: Literal["query", "intake"],
        operation: Literal["start", "pause", "update"],
        data: Change,
        request: Request,
    ) -> Any:
        authorize = authorization(config, request, identity)
        with database.locked(config):
            authorize()
            if not policy.managed(config, identity):
                raise InvalidInput("channel_services_activation_required")
        if operation == "update":
            return control.start(
                config,
                identity,
                role,
                data.expected_revision,
                authorize=authorize,
                image=runtime_image.pin(config),
            )
        if operation == "start":
            return control.start(
                config, identity, role, data.expected_revision, authorize=authorize
            )
        return control.pause(config, identity, role, data.expected_revision, authorize=authorize)

    return routes
