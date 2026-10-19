"""Authenticated existing-MOC adoption transport; no filesystem logic."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import Field, StrictBool

from . import moc_registration
from .config import Config, IntegrityError
from .payloads import Identifier, Payload
from .query_http import access


class Scope(Payload):
    client_id: Identifier
    vault_id: Identifier


class Preview(Scope):
    path: Annotated[str, Field(min_length=1, max_length=1024)]
    expected_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class Confirm(Scope):
    confirm: StrictBool


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/mocs")

    @routes.get("")
    def listing(
        request: Request,
        client_id: str = Query(max_length=36),
        vault_id: str = Query(max_length=36),
    ) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return moc_registration.listing(config, db, client_id, vault_id)

    @routes.post("/preview")
    def preview(request: Request, data: Preview) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return moc_registration.preview(
                config, db, data.client_id, data.vault_id, data.path, data.expected_sha256
            )

    @routes.post("/{operation_id}/{action}")
    def action(
        request: Request,
        operation_id: str,
        action: Literal["apply", "reconcile", "cancel"],
        data: Confirm,
    ) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            if not data.confirm:
                raise IntegrityError("placement_confirmation_required")
            return moc_registration.apply(
                config, db, data.client_id, data.vault_id, operation_id, action
            )

    return routes
