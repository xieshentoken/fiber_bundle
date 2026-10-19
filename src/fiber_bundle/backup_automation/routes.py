"""Admin/CSRF-protected policy and asynchronous backup operations; no restore activation."""

import time
from typing import Any

from fastapi import APIRouter, Request

from ..config import Config
from ..payloads import Identifier, Payload
from ..query_http import access
from . import retention, store
from .policy import Save


class Pin(Payload):
    pinned: bool


def router(config: Config) -> APIRouter:
    routes = APIRouter()

    @routes.get("/admin/backup-automation/overview")
    def overview(request: Request) -> list[dict[str, Any]]:
        with access(config, request, admin=True):
            value = store.status(config)
            latest = next((j for j in value["jobs"] if j["state"] != "deleted"), None)
            if latest:
                names = {
                    "succeeded": "backup_verified",
                    "failed": "backup_failed",
                    "queued": "backup_waiting",
                    "running": "backup_running",
                    "deleting": "backup_cleanup_pending",
                    "cleanup_interrupted": "backup_cleanup_pending",
                }
                return [{"state": names[latest["state"]], "id": latest["id"]}]
            from ..backup_requests import list_requests

            return list_requests(config)

    @routes.get("/admin/backup-automation")
    def status(request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return store.status(config)

    @routes.put("/admin/backup-automation")
    def configure(data: Save, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return store.save(config, data)

    @routes.post("/admin/backup-automation/jobs", status_code=202)
    def manual(request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return store.enqueue(config, "manual")

    @routes.put("/admin/backup-automation/jobs/{job_id}/pin")
    def pin(job_id: Identifier, data: Pin, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return store.pin(config, job_id, data.pinned)

    @routes.post("/admin/backup-automation/cleanup")
    def cleanup(request: Request) -> dict[str, int]:
        with access(config, request, admin=True):
            return retention.clean(config, now=time.time())

    return routes
