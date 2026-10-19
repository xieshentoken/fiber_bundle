"""Thin authenticated lifecycle transport; process management stays in channel modules."""

from typing import Any

from fastapi import APIRouter, Request

from ..config import Config, InvalidInput
from ..query_http import access
from . import health, legacy_control, lifecycle, operations, store


def router(config: Config) -> APIRouter:
    routes = APIRouter()

    @routes.post("/{identity}/lifecycle/preview")
    def preview(identity: str, data: lifecycle.Preview, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return lifecycle.preview(config, identity, data.action)

    @routes.post("/{identity}/lifecycle", status_code=202)
    def begin(identity: str, data: lifecycle.Request, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (main, _):
            return lifecycle.begin(config, main, identity, data)

    @routes.post("/{identity}/lifecycle/{operation_id}/resume", status_code=202)
    def resume(identity: str, operation_id: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return lifecycle.resume(config, identity, operation_id)

    @routes.post("/{identity}/legacy-start", status_code=202)
    def start(identity: str, data: lifecycle.Start, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True), store.opened(config) as db:
            channel = operations.entry(config, db, identity)
            if channel.get("origin") != "legacy" or channel["state"] not in {"paused", "error"}:
                raise InvalidInput("channel_pause_required")
            if operations.pending(db, identity):
                raise InvalidInput("channel_operation_pending")
            if (
                channel["revision"] != data.expected_revision
                or health.fingerprint(channel) != data.configuration_sha256
            ):
                raise InvalidInput("channel_revision_conflict")
            if lifecycle.runtime(config, channel)["process_state"] != "stopped":
                raise InvalidInput("channel_instance_already_running")
            operations.set_entry_state(db, channel, "running")
            try:
                legacy_control.start(config, channel)
            except (OSError, InvalidInput):
                operations.set_entry_state(db, channel, "error")
                raise
            return {"state": "starting", "id": identity}

    return routes
