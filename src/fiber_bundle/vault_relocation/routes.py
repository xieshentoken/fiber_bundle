"""Admin-only, CSRF-protected transport for explicit location changes."""

from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import Field, field_validator

from ..config import Config
from ..payloads import Identifier, Payload
from ..query_http import access
from . import execution, plan, store


class Preview(Payload):
    vault_id: Identifier
    destination: str = Field(min_length=1, max_length=4096)


class Confirmation(Payload):
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Start(Confirmation):
    external_writers_stopped: Literal[True]

    @field_validator("external_writers_stopped", mode="before")
    @classmethod
    def acknowledged(cls, value: object) -> object:
        if value is not True:
            raise ValueError("explicit_boolean_required")
        return value


class Recovery(Payload):
    action: Literal["resume", "restore"]
    recovery_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/admin/vault-relocations")

    @routes.post("/preview")
    def preview(data: Preview, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return plan.preview(config, data.vault_id, data.destination)

    @routes.get("")
    def listing(request: Request, vault_id: str | None = None) -> list[dict[str, Any]]:
        with access(config, request, admin=True) as (db, _):
            return [
                store.view(db, r[0])
                for r in db.execute(
                    "SELECT id FROM vault_operations WHERE kind='vault_relocation' "
                    "AND (? IS NULL OR vault_id=?) ORDER BY created_at DESC LIMIT 100",
                    (vault_id, vault_id),
                )
            ]

    @routes.get("/{identity}")
    def status(identity: Identifier, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return store.view(db, identity)

    @routes.post("/{identity}/start")
    def start(identity: Identifier, data: Start, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return execution.start(config, identity, data.plan_sha256)

    @routes.post("/{identity}/stop")
    def stop(identity: Identifier, data: Confirmation, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return execution.stop(config, identity, data.plan_sha256)

    @routes.post("/{identity}/recovery-preview")
    def recovery_preview(identity: Identifier, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return execution.recovery_preview(config, identity)

    @routes.post("/{identity}/recover")
    def recover(identity: Identifier, data: Recovery, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return execution.recover(config, identity, data.action, data.recovery_sha256)

    return routes
