"""Admin-only location repair after external Vault changes."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request
from pydantic import Field, StrictBool

from . import publication_rebind, publication_reconcile, publication_reconcile_scan
from .config import Config, IntegrityError
from .note_placement_routes import Confirm, Scope
from .payloads import Identifier, Payload
from .query_http import access


class Preview(Scope):
    kind: Literal["article", "document", "media", "expert", "intake"]
    publication_id: Identifier
    expected_revision: Annotated[int, Field(ge=1)]
    entry_note_path: Annotated[str, Field(min_length=1, max_length=1024)]


class Choice(Payload):
    kind: Literal["article", "document", "media", "expert", "intake", "note"]
    publication_id: Identifier
    expected_revision: Annotated[int, Field(ge=1)]
    action: Literal["rebind", "missing"]
    target_path: Annotated[str | None, Field(min_length=1, max_length=1024)] = None
    deletion_confirmed: StrictBool = False


class BatchPreview(Scope):
    items: Annotated[list[Choice], Field(min_length=1, max_length=1000)]


class BatchConfirm(Confirm):
    max_items: Annotated[int, Field(ge=1, le=1000)] = 1000


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/admin/vault-publication-rebinds")

    @routes.post("/scan")
    def scan(request: Request, data: Scope) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return publication_reconcile_scan.scan(config, db, data.client_id, data.vault_id)

    @routes.post("/batch/preview")
    def batch_preview(request: Request, data: BatchPreview) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return publication_reconcile.preview(
                config,
                db,
                data.client_id,
                data.vault_id,
                [item.model_dump() for item in data.items],
            )

    @routes.get("/batch/{operation}")
    def batch_read(
        request: Request, operation: Identifier, client_id: Identifier, vault_id: Identifier
    ) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return publication_reconcile.result(
                *publication_reconcile.get(db, client_id, vault_id, operation)
            )

    @routes.post("/batch/{operation}/apply")
    def batch_apply(request: Request, operation: Identifier, data: BatchConfirm) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            if not data.confirm:
                raise IntegrityError("placement_confirmation_required")
            return publication_reconcile.apply(
                config, db, data.client_id, data.vault_id, operation, max_items=data.max_items
            )

    @routes.post("/preview")
    def preview(request: Request, data: Preview) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return publication_rebind.preview(
                config,
                db,
                data.client_id,
                data.vault_id,
                data.kind,
                data.publication_id,
                data.expected_revision,
                data.entry_note_path,
            )

    @routes.post("/{operation}/{action}")
    def apply(
        request: Request,
        operation: Identifier,
        action: Literal["apply", "reconcile", "cancel"],
        data: Confirm,
    ) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            if not data.confirm:
                raise IntegrityError("placement_confirmation_required")
            return publication_rebind.apply(
                config,
                db,
                data.client_id,
                data.vault_id,
                operation,
                reconcile=action == "reconcile",
                cancel=action == "cancel",
            )

    return routes
