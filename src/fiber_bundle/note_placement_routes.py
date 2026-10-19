"""Admin-only placement transport. Business and filesystem access remain outside routes."""

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import Field, StrictBool

from . import auth, note_placement, note_placement_execution
from .config import Config, IntegrityError
from .payloads import Identifier, Payload
from .query_http import access


class Scope(Payload):
    client_id: Identifier
    vault_id: Identifier


class Preview(Scope):
    note_identity: Annotated[str, Field(min_length=1, max_length=100)]
    expected_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    action: Literal[
        "archive", "link_archive", "associate_only", "expert_archive", "expert_link_archive"
    ] = "archive"
    moc_id: Identifier | None = None
    moc_revision: Annotated[int | None, Field(ge=1)] = None
    destination: Annotated[str | None, Field(max_length=1024)] = None


class MissingPreview(Scope):
    kind: Literal["article", "document", "media", "expert", "intake"]
    publication_id: Identifier
    expected_revision: Annotated[int, Field(ge=1)]


class Confirm(Scope):
    confirm: StrictBool


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/admin/vault-placements")

    from .moc_registration_routes import router as moc_router

    routes.include_router(moc_router(config))

    @routes.post("/missing/preview")
    def missing_preview(request: Request, data: MissingPreview) -> dict[str, Any]:
        from . import publication_availability

        with access(config, request, admin=True) as (db, _):
            return publication_availability.preview(
                config,
                db,
                data.client_id,
                data.vault_id,
                data.kind,
                data.publication_id,
                data.expected_revision,
            )

    @routes.post("/missing/{operation_id}/apply")
    def missing_apply(request: Request, operation_id: str, data: Confirm) -> dict[str, Any]:
        from . import publication_availability

        with access(config, request, admin=True) as (db, _):
            if not data.confirm:
                raise IntegrityError("placement_confirmation_required")
            return publication_availability.apply(
                config, db, data.client_id, data.vault_id, operation_id
            )

    @routes.get("")
    def listing(
        request: Request,
        client_id: str = Query(max_length=36),
        vault_id: str = Query(max_length=36),
    ) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return note_placement.listing(config, db, client_id, vault_id)

    @routes.post("/preview")
    def preview(request: Request, data: Preview) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return note_placement.preview(
                config,
                db,
                data.client_id,
                data.vault_id,
                data.note_identity,
                data.expected_sha256,
                action=data.action,
                moc_id=data.moc_id,
                moc_revision=data.moc_revision,
                destination=data.destination,
            )

    @routes.post("/automatic/{operation_id}/{action}")
    def automatic(
        request: Request, operation_id: str, action: Literal["cancel", "reconcile"], data: Confirm
    ) -> dict[str, str]:
        from .automatic_placement import reconcile

        with access(config, request, admin=True) as (db, _):
            if not data.confirm:
                raise IntegrityError("placement_confirmation_required")
            return reconcile(config, db, data.client_id, data.vault_id, operation_id, action)

    @routes.post("/{operation_id}/{action}")
    def apply(
        request: Request,
        operation_id: str,
        action: Literal["apply", "reconcile", "cancel"],
        data: Confirm,
    ) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            if not data.confirm:
                raise IntegrityError("placement_confirmation_required")
            if action == "cancel":
                row = db.execute(
                    "SELECT payload,state FROM vault_operations WHERE id=? AND vault_id=? "
                    "AND kind='note_placement'",
                    (operation_id, data.vault_id),
                ).fetchone()
                if not row or json.loads(row["payload"])["client_id"] != data.client_id:
                    raise auth.Denied(404)
                auth.grant(db, data.client_id, data.vault_id, "add")
                if row["state"] not in {"prepared", "cancelled"}:
                    raise IntegrityError("placement_reconciliation_required")
                db.execute(
                    "UPDATE vault_operations SET state='cancelled' WHERE id=?", (operation_id,)
                )
                return {"state": "cancelled"}
            return note_placement_execution.apply(
                config,
                db,
                data.client_id,
                data.vault_id,
                operation_id,
                reconcile=action == "reconcile",
            )

    return routes
