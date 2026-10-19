"""Authenticated lifecycle controls over the owned backend; no arbitrary process API."""

from typing import Any

from fastapi import APIRouter, Request
from pydantic import StrictBool

from . import batch_reminders, execution_scheduler
from .backend_lifecycle import BackendControl
from .config import Config, InvalidInput
from .payloads import Identifier, Payload
from .query_http import access


class Reminder(Payload):
    enabled: StrictBool


class Stop(Payload):
    instance_id: Identifier


def router(config: Config, lifecycle: BackendControl | None) -> APIRouter:
    routes = APIRouter(prefix="/admin/backend")

    @routes.get("")
    def status(request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return {
                **(
                    {"state": "running", "managed": False, "receiver": "external_not_managed"}
                    if lifecycle is None
                    else lifecycle.status()
                ),
                "queue": execution_scheduler.snapshot(config),
                "reminder_history": batch_reminders.history(config),
            }

    @routes.post("/stop")
    def stop(data: Stop, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            if lifecycle is None:
                raise InvalidInput("backend_not_managed")
            return lifecycle.stop(db, data.instance_id)

    @routes.get("/reminders")
    def reminders(request: Request) -> list[dict[str, Any]]:
        with access(config, request, admin=True):
            return batch_reminders.settings(config)

    @routes.put("/reminders/{batch_id}")
    def reminder(batch_id: str, data: Reminder, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return batch_reminders.set_enabled(config, batch_id, data.enabled)

    return routes
