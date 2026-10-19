"""Cookie-authenticated document management reuses the owned document contracts."""

from typing import Any

from fastapi import APIRouter, Request, Response

from . import auth
from . import document_creation_artifacts as artifacts
from . import document_creation_binding as binding
from . import document_creation_continuation as continuation
from . import document_creation_delivery as delivery
from . import document_creation_ledger as ledger
from . import document_creation_save as save
from . import document_creation_tasks as tasks
from .config import Config
from .document_creation_routes import Append, Revision, Save, Submit
from .payloads import Identifier
from .query_http import access, active


class AdminSubmit(Submit):
    client_id: Identifier


def owner(config: Config, task_id: str) -> str:
    with ledger.connection(config) as db:
        row = (
            None
            if db is None
            else db.execute("SELECT client_id FROM tasks WHERE id=?", (task_id,)).fetchone()
        )
        if row is None:
            raise auth.Denied(404)
        return str(row[0])


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/admin/document-tasks")

    @routes.get("")
    def listing(request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            with ledger.connection(config) as db:
                rows = (
                    []
                    if db is None
                    else db.execute(
                        "SELECT id AS task_id,client_id,state,head_revision AS revision,"
                        "head_sha256 AS "
                        "plan_sha256,created_at FROM tasks ORDER BY created_at DESC,id LIMIT 100"
                    ).fetchall()
                )
                return {"tasks": [dict(row) for row in rows]}

    @routes.post("")
    def submit(data: AdminSubmit, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return tasks.submit(
                config,
                db,
                data.client_id,
                data.submission_id,
                data.query_task_id,
                data.requirements,
                data.requested_format,
            )

    @routes.get("/{task_id}")
    def status(task_id: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            cid = owner(config, task_id)
            value = tasks.status(config, db, cid, task_id)
            if binding.get(db, task_id) is not None:
                value["file_delivery"] = delivery.status(db, cid, task_id)
            value["versions"] = continuation.versions(config, db, cid, task_id)
            return value

    @routes.get("/{task_id}/draft")
    def draft(task_id: str, request: Request) -> Response:
        with access(config, request, admin=True) as (db, _):
            raw, metadata = artifacts.get(config, db, owner(config, task_id), task_id)
            return Response(
                raw,
                media_type=metadata["artifact"]["media_type"],
                headers={
                    "Content-Disposition": 'attachment; filename="draft.'
                    + metadata["artifact"]["original_name"].rsplit(".", 1)[-1]
                    + '"'
                },
            )

    @routes.post("/{task_id}/append")
    def append(task_id: str, data: Append, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            cid = owner(config, task_id)
            if binding.get(db, task_id) is None:
                from .config import InvalidInput

                if (
                    data.updated_query_task_id is not None
                    or data.expected_update_sha256 is not None
                ):
                    raise InvalidInput("document_prepared_input_update_requires_new_task")
                return tasks.amend(
                    config,
                    db,
                    cid,
                    task_id,
                    data.addition,
                    expected_revision=data.expected_revision,
                    expected_sha256=data.expected_sha256,
                )
            return continuation.enqueue(
                config,
                db,
                cid,
                task_id,
                data.amendment_id,
                data.addition,
                expected_revision=data.expected_revision,
                expected_sha256=data.expected_sha256,
                updated_query_task_id=data.updated_query_task_id,
                expected_update_sha256=data.expected_update_sha256,
            )

    @routes.get("/{task_id}/input-update/{query_task_id}")
    def update(task_id: str, query_task_id: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return continuation.update_preview(
                config, db, owner(config, task_id), task_id, query_task_id
            )

    @routes.post("/{task_id}/cancel")
    def cancel(task_id: str, data: Revision, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return continuation.cancel_all(
                config,
                db,
                owner(config, task_id),
                task_id,
                data.expected_revision,
                data.expected_sha256,
            )

    @routes.post("/{task_id}/save")
    def save_analysis(task_id: str, data: Save, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            cid = owner(config, task_id)
            active(db, cid)
            prepared = save.prepare(
                config,
                db,
                cid,
                task_id,
                data.target_vault_id,
                expected_artifact_sha256=data.expected_artifact_sha256,
                allow_save=True,
            )
            if prepared["state"] == "scope_approval_required":
                return prepared
            return save.publish(config, db, cid, task_id, allow_save=True)

    return routes
