"""Administrator-only supplier workspace transport; business logic stays in the domain."""

from typing import Any

from fastapi import APIRouter, Request, Response

from ..config import Config
from ..download_names import disposition, filename
from ..query_http import access
from . import commands, ingestion, query, read, service
from .contracts import (
    Correction,
    DocumentImport,
    ImportLegacy,
    JobAction,
    ManualQuote,
    OwnedCompare,
    OwnedCondition,
    SavePolicy,
    Scope,
)


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/admin/suppliers")

    @routes.get("")
    def overview(client_id: str, vault_id: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return read.summary(config, db, client_id, vault_id)

    @routes.post("/legacy/preview")
    def preview(data: Scope, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            result = service.preview(config, db, data.client_id, data.vault_id)
            return {k: v for k, v in result.items() if k not in {"notes", "records"}}

    @routes.post("/legacy/import")
    def historical(data: ImportLegacy, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return service.import_legacy(
                config, db, data.client_id, data.vault_id, data.request_id, data.expected_sha256
            )

    @routes.post("/policy")
    def policy(data: SavePolicy, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return service.save_policy(config, db, data)

    @routes.post("/manual")
    def manual(data: ManualQuote, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return ingestion.manual(config, db, data)

    @routes.post("/document", status_code=202)
    def document(data: DocumentImport, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return ingestion.document(config, db, data)

    @routes.post("/jobs/{identity}/{action}")
    def action(identity: str, action: str, data: JobAction, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return commands.job_action(
                config, db, data.client_id, data.vault_id, identity, data.expected_sha256, action
            )

    @routes.post("/correction")
    def correct(data: Correction, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return commands.correct(config, db, data)

    @routes.get("/evidence/{identity}")
    def evidence(identity: str, client_id: str, vault_id: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return read.evidence(config, db, client_id, vault_id, identity)

    @routes.get("/original/{identity}")
    def original(
        identity: str, client_id: str, vault_id: str, request: Request, asset_id: str | None = None
    ) -> Response:
        with access(config, request, admin=True) as (db, _):
            content, name, mime = read.original(config, db, client_id, vault_id, identity, asset_id)
            return Response(
                content,
                media_type=mime,
                headers={"Content-Disposition": disposition(filename(name, mime))},
            )

    @routes.get("/markdown/{identity}")
    def markdown(identity: str, client_id: str, vault_id: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return {
                "markdown": read.markdown(config, db, client_id, vault_id, identity),
                "vault_files_written": False,
            }

    @routes.post("/query")
    def select(data: OwnedCondition, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return query.select(config, db, data.client_id, data)

    @routes.post("/compare")
    def compare(data: OwnedCompare, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return query.compare(config, db, data.client_id, data)

    @routes.post("/classification-preview")
    def classifications(data: Scope, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return query.classifications(config, db, data.client_id, data.vault_id)

    return routes
