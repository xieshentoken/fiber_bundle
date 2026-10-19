"""Small HTTP routing layer for the authorized local storage operations."""

import re
import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Annotated, Any

from fastapi import FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from fiber_bundle import auth, build_identity, database, operations, vaults
from fiber_bundle.admin_routes import router
from fiber_bundle.answer_routes import router as answer_router
from fiber_bundle.answer_submission_routes import router as answer_submission_router
from fiber_bundle.backend_lifecycle import BackendControl
from fiber_bundle.backup_admin_routes import router as backup_router
from fiber_bundle.config import Config, InvalidInput, StorageError
from fiber_bundle.desktop_routes import router as desktop_router
from fiber_bundle.document_admin_routes import router as document_admin_router
from fiber_bundle.document_creation_admin_routes import router as creation_admin_router
from fiber_bundle.document_creation_routes import router as creation_router
from fiber_bundle.document_routes import router as document_router
from fiber_bundle.execution_config import ExecutionConfig
from fiber_bundle.http_guard import Guard, Settings
from fiber_bundle.management_routes import router as management_router
from fiber_bundle.media_admin_routes import router as media_admin_router
from fiber_bundle.media_glossary_routes import router as media_glossary_router
from fiber_bundle.payloads import Intake, Named
from fiber_bundle.search_routes import router as search_router
from fiber_bundle.source_admin_routes import router as source_admin_router
from fiber_bundle.source_import_routes import router as source_import_router
from fiber_bundle.store import EventConflict
from fiber_bundle.system_admin_routes import router as system_router
from fiber_bundle.web_routes import router as web_router


def create_app(
    config: Config,
    settings: Settings | None = None,
    *,
    execution_policy: ExecutionConfig | None = None,
    lifecycle: BackendControl | None = None,
) -> FastAPI:
    if execution_policy is not None:
        execution_policy.validate()
    settings = settings or Settings()
    settings.validate()
    config.validate()
    db = database.connect(config.state_root)  # Refuse v1 before listening; migrate explicitly.
    db.close()
    identity = build_identity.inspect()
    from .agent_connections import store as agent_store
    from .agent_connections.routes import router as agent_router
    from .agent_connections.runtime import Runtime as AgentRuntime

    agent_store.recover(config)
    agent_runtime = AgentRuntime(config, settings.origin)

    from .automatic_ingestion import Runtime as AutomaticRuntime

    automatic_runtime = AutomaticRuntime(config)
    from .model_configuration.testing import Runtime as ModelTestRuntime

    model_test_runtime = ModelTestRuntime(config)
    from .vault_relocation.runtime import Runtime as RelocationRuntime

    relocation_runtime = RelocationRuntime(config)
    from .backup_automation.runtime import Runtime as BackupRuntime

    backup_runtime = BackupRuntime(config)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        backup_runtime.start()
        relocation_runtime.start()
        automatic_runtime.start()
        try:
            yield
        finally:
            backup_runtime.close()
            relocation_runtime.close()
            automatic_runtime.close()
            agent_runtime.close()
            model_test_runtime.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(Guard, config=config, settings=settings)
    from .agent_connections.access_routes import router as agent_access_router
    from .agent_connections.knowledge_admin_routes import router as knowledge_admin_router
    from .agent_connections.knowledge_routes import router as agent_knowledge_router

    app.include_router(knowledge_admin_router(config))
    app.include_router(agent_knowledge_router(config))
    app.include_router(agent_access_router(config))
    from .skill_export_routes import router as skill_export_router

    app.include_router(skill_export_router(config))
    app.include_router(agent_router(config, agent_runtime))
    app.include_router(router(config, settings))
    app.include_router(system_router(config, settings, execution_policy))
    from .model_configuration.routes import router as model_configuration_router

    app.include_router(model_configuration_router(config, model_test_runtime))
    from .vault_catalog_routes import router as vault_catalog_router

    app.include_router(vault_catalog_router(config))
    from .vault_save_routes import router as vault_save_router

    app.include_router(vault_save_router(config))
    from .vault_merge_routes import router as vault_merge_router

    app.include_router(vault_merge_router(config))
    from .vault_relocation.routes import router as relocation_router

    app.include_router(relocation_router(config))

    from .automatic_routes import router as automatic_router

    app.include_router(automatic_router(config))
    from .note_placement_routes import router as placement_router
    from .publication_preview_routes import router as publication_preview_router
    from .publication_rebind_routes import router as publication_rebind_router
    from .vault_layout_migration_routes import router as layout_migration_router

    app.include_router(publication_preview_router(config))
    app.include_router(publication_rebind_router(config))
    app.include_router(placement_router(config))
    app.include_router(layout_migration_router(config))
    from .channels.routes import router as channel_router

    app.include_router(channel_router(config))
    from . import query_admin_routes, query_routes

    app.include_router(query_routes.router(config))
    app.include_router(query_admin_routes.router(config, lifecycle))
    app.include_router(desktop_router(config, lifecycle))
    app.include_router(backup_router(config))
    from .backup_automation.routes import router as backup_automation_router

    app.include_router(backup_automation_router(config))
    app.include_router(management_router(config, settings))
    app.include_router(creation_admin_router(config))
    app.include_router(web_router())
    app.include_router(search_router(config))
    app.include_router(answer_router(config))
    app.include_router(answer_submission_router(config, execution_policy))
    app.include_router(document_router(config))
    app.include_router(creation_router(config))
    app.include_router(document_admin_router(config))
    app.include_router(source_admin_router(config, execution_policy))
    app.include_router(source_import_router(config))
    from .document_split import SplitRequired
    from .note_search_cache import IndexUnavailable
    from .note_search_routes import router as note_search_router

    app.include_router(note_search_router(config))
    from .note_search_admin import router as note_index_admin_router

    app.include_router(note_index_admin_router(config))
    from .knowledge_graph.routes import router as knowledge_graph_router

    app.include_router(knowledge_graph_router(config))
    from .organization.routes import router as organization_router

    app.include_router(organization_router(config))
    from .knowledge_graph.projection_routes import router as projection_router

    app.include_router(projection_router(config))
    from .delivery_reconciliation import router as reconciliation_router

    app.include_router(reconciliation_router(config))
    from .video_routes import router as video_router

    app.include_router(video_router(config))
    app.include_router(media_admin_router(config))
    app.include_router(media_glossary_router(config))

    async def bounded_error(request: Request, exc: Exception) -> JSONResponse:
        from .model_configuration.errors import MESSAGES as model_messages
        from .model_configuration.errors import ModelError

        if isinstance(exc, InvalidInput) and str(exc) in model_messages:
            exc = ModelError(str(exc))

        if isinstance(exc, ModelError):
            return JSONResponse(
                {"error": exc.code, **exc.public()},
                status_code=409
                if exc.code in {"model_revision_conflict", "model_referenced"}
                else 422,
            )
        if isinstance(exc, RequestValidationError) and request.url.path.startswith("/admin/models"):
            issue = exc.errors()[0]
            return JSONResponse(
                {
                    "error": "model_invalid_input",
                    "message": "输入不符合字段要求，请检查标记的配置项。",
                    "field": str(issue["loc"][-1]),
                },
                status_code=422,
            )
        from .channels.errors import response as channel_error
        from .processing_errors import response as processing_error
        from .vault_errors import response as vault_error
        from .vault_save_contract import SettingsError

        processing_specific = processing_error(exc)
        if processing_specific is not None:
            processing_status, processing_body = processing_specific
            return JSONResponse(processing_body, status_code=processing_status)
        specific = None
        if request.url.path.startswith("/admin/channels"):
            specific = channel_error(exc)
        elif request.url.path.startswith("/admin/vault-catalog"):
            specific = vault_error(exc)
        if specific is not None:
            status, body = specific
            return JSONResponse(body, status_code=status)

        from .vault_relocation.errors import RelocationError

        if isinstance(exc, RelocationError):
            return JSONResponse({"error": str(exc)}, status_code=exc.status)

        if isinstance(exc, auth.Denied):
            status, code = exc.status, "request_denied"
        elif isinstance(exc, SettingsError):
            status, code = exc.status, str(exc)
        elif isinstance(exc, agent_store.Conflict):
            status, code = 409, str(exc)
        elif isinstance(exc, EventConflict):
            status, code = 409, "event_conflict"
        elif isinstance(exc, (IndexUnavailable, SplitRequired)):
            status, code = 409, str(exc)
        elif isinstance(exc, (InvalidInput, RequestValidationError)):
            status, code = 422, "invalid_input"
            if (
                isinstance(exc, InvalidInput)
                and re.fullmatch(r"/admin/channels/[^/]+/intake(?:/.*)?", request.url.path)
                and re.fullmatch(r"intake_[a-z_]{1,80}", str(exc))
            ):
                code = str(exc)
                if code == "intake_revision_conflict":
                    status = 409
            if (
                request.url.path == "/admin/backups"
                and isinstance(exc, InvalidInput)
                and str(exc) == "backup_unclassified_state"
            ):
                code = "backup_unclassified_state"
            if request.url.path.startswith("/admin/backup-automation") and re.fullmatch(
                r"backup_[a-z_]{1,80}", str(exc)
            ):
                code = str(exc)
                if code in {"backup_policy_changed", "backup_job_busy"}:
                    status = 409
            if re.fullmatch(r"(?:placement|layout)_[a-z_]{1,80}", str(exc)):
                status, code = 409, str(exc)
        elif isinstance(exc, HTTPException):
            status, code = exc.status_code, "request_denied"
        elif isinstance(exc, StorageError) and re.fullmatch(
            r"(?:placement|layout)_[a-z_]{1,80}", str(exc)
        ):
            status, code = 409, str(exc)
        else:
            status, code = 503, "storage_unavailable"
        return JSONResponse({"error": code}, status_code=status)

    for error in (
        auth.Denied,
        agent_store.Conflict,
        StorageError,
        OSError,
        sqlite3.Error,
        RequestValidationError,
        HTTPException,
    ):
        app.add_exception_handler(error, bounded_error)

    @contextmanager
    def access(request: Request) -> Iterator[tuple[sqlite3.Connection, str]]:
        db = database.connect(config.state_root)
        try:
            client = auth.client(db, request.headers.get("authorization", ""))
            yield db, client["id"]
        finally:
            db.close()

    @app.get("/health")
    def health() -> dict[str, object]:
        return {"status": "ok", "build": identity}

    @app.get("/v1/vaults")
    def list_vaults(request: Request) -> list[dict[str, object]]:
        with access(request) as (db, client_id):
            return vaults.client_vaults(db, client_id)

    @app.post("/v1/vaults", status_code=201)
    def new_vault(data: Named, request: Request) -> dict[str, str]:
        with access(request) as (db, client_id):
            identity = vaults.request_vault(config, db, client_id, data.name)
            return {"id": identity, "status": "pending"}

    @app.post("/v1/receipts", status_code=202)
    def intake(data: Intake, request: Request) -> dict[str, Any]:
        with access(request) as (db, client_id):
            return operations.intake(config, db, client_id, data)

    @app.get("/v1/receipts/{job_id}")
    def receipt(job_id: str, request: Request) -> dict[str, Any]:
        with access(request) as (db, client_id):
            return operations.receipt_status(config, db, client_id, job_id)

    @app.get("/v1/vaults/{vault_id}/versions")
    def versions(
        vault_id: str,
        request: Request,
        offset: Annotated[int, Query(ge=0, le=1000000)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> list[dict[str, Any]]:
        with access(request) as (db, client_id):
            return operations.versions(config, db, client_id, vault_id, offset, limit)

    @app.get("/v1/vaults/{vault_id}/versions/{version_id}/assets/{asset_id}")
    def original(vault_id: str, version_id: str, asset_id: str, request: Request) -> Response:
        with access(request) as (db, client_id):
            data = operations.original(
                config, db, client_id, vault_id, version_id, asset_id, settings.max_original_bytes
            )
            from .download_names import disposition

            name, mime = operations.original_metadata(config, db, vault_id, version_id, asset_id)
            return Response(
                data,
                media_type=mime,
                headers={
                    "Content-Disposition": disposition(name),
                },
            )

    return app
