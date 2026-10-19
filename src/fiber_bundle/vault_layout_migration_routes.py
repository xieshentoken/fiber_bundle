"""Explicit selected legacy conversion, sharing the admin placement confirmation contract."""

import json
from typing import Any, Literal

from fastapi import APIRouter, Query, Request

from . import auth, vault_layout_migration
from .config import Config, IntegrityError
from .note_placement_routes import Confirm, Scope
from .payloads import Identifier
from .query_http import access


class Preview(Scope):
    kind: Literal["article", "document", "media", "expert", "intake"]
    publication_id: Identifier


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/admin/vault-layout-migrations")

    @routes.get("")
    def listing(
        request: Request,
        client_id: str = Query(max_length=36),
        vault_id: str = Query(max_length=36),
    ) -> list[dict[str, Any]]:
        with access(config, request, admin=True) as (db, _):
            return vault_layout_migration.candidates(config, db, client_id, vault_id)

    @routes.post("/preview")
    def preview(request: Request, data: Preview) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return vault_layout_migration.preview(
                config, db, data.client_id, data.vault_id, data.kind, data.publication_id
            )

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
                    "AND kind='layout_migration'",
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
            return vault_layout_migration.apply(
                config,
                db,
                data.client_id,
                data.vault_id,
                operation_id,
                reconcile=action == "reconcile",
            )

    return routes
