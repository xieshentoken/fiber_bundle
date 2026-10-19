"""Local administrator inventory, bounded downloads and explicit reconciliation."""

from contextlib import ExitStack
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response

from . import (
    article_tasks,
    auth,
    document_tasks,
    execution,
    execution_ledger,
    management_inventory,
    operations,
)
from .config import Config
from .executor_container import run_lock
from .http_guard import Settings
from .query_http import access, active


def router(config: Config, settings: Settings) -> APIRouter:
    routes = APIRouter(prefix="/admin/inventory")

    @routes.get("/receipts")
    def receipts(request: Request) -> list[dict[str, Any]]:
        with access(config, request, admin=True) as (db, _):
            return management_inventory.receipts(db)

    @routes.get("/executions")
    def executions(request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return management_inventory.executions(db)

    @routes.post("/executions/{run_id}/cancel")
    def cancel(run_id: str, request: Request) -> dict[str, str]:
        with access(config, request, admin=True) as (db, _):
            row = db.execute("SELECT id FROM execution_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise auth.Denied(404)
            with db:
                db.execute(
                    "UPDATE model_calls SET state='uncertain' WHERE run_id=? AND state='reserved'",
                    (run_id,),
                )
                execution_ledger.transition(db, run_id, "cancelled", "cancelled")
            return {"state": execution_ledger.get_run(db, run_id)["state"], "run_id": run_id}

    @routes.post("/executions/{run_id}/reconcile")
    def reconcile(run_id: str, request: Request) -> dict[str, str]:
        with access(config, request, admin=True) as (db, _):
            if db.execute("SELECT id FROM execution_runs WHERE id=?", (run_id,)).fetchone() is None:
                raise auth.Denied(404)
            with ExitStack() as locks:
                document = db.execute(
                    "SELECT task_id FROM document_creation_runs WHERE run_id=?", (run_id,)
                ).fetchone()
                if document is not None:
                    locks.enter_context(run_lock(config, document["task_id"]))
                locks.enter_context(run_lock(config, run_id))
                # A live owner refuses this lock; only its abandoned exact container is stopped.
                execution.stop(run_id)
                article_tasks.stop_locked(config, db, run_id)
                document_tasks.stop_locked(config, db, run_id)
                row = execution_ledger.get_run(db, run_id)
                if row["state"] == "running":
                    execution_ledger.interrupt(db, run_id)
            return {"state": execution_ledger.get_run(db, run_id)["state"], "run_id": run_id}

    @routes.get("/components")
    def components(request: Request) -> dict[str, Any]:
        from .gui_capabilities import declared

        with access(config, request, admin=True) as (db, _):
            return {
                **management_inventory.components(),
                "gui_features": declared(db, request.app.openapi().get("paths", {})),
            }

    @routes.post("/components/check")
    def check_components(request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return management_inventory.check_components()

    @routes.get("/versions/{vault_id}")
    def versions(
        vault_id: str,
        client_id: str,
        request: Request,
        offset: Annotated[int, Query(ge=0, le=1000000)] = 0,
    ) -> list[dict[str, Any]]:
        with access(config, request, admin=True) as (db, _):
            active(db, client_id)
            return management_inventory.versions(config, db, client_id, vault_id, offset)

    @routes.get("/original/{vault_id}/{version_id}/{asset_id}")
    def original(
        vault_id: str, version_id: str, asset_id: str, client_id: str, request: Request
    ) -> Response:
        with access(config, request, admin=True) as (db, _):
            data = operations.original(
                config, db, client_id, vault_id, version_id, asset_id, settings.max_original_bytes
            )
            from .download_names import disposition

            name, mime = operations.original_metadata(config, db, vault_id, version_id, asset_id)
            return Response(
                data,
                media_type=mime,
                headers={"Content-Disposition": disposition(name)},
            )

    return routes
