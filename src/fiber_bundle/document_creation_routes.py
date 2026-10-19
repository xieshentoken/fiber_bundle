"""Authenticated document intents and immutable results; execution remains a local worker."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import Field

from . import auth, database
from . import document_creation_artifacts as artifacts
from . import document_creation_binding as binding
from . import document_creation_continuation as continuation
from . import document_creation_delivery as delivery
from . import document_creation_save as save
from . import document_creation_tasks as tasks
from .config import Config, InvalidInput
from .payloads import Identifier, Payload

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Requirement = Annotated[str, Field(min_length=1, max_length=16384)]


class Submit(Payload):
    submission_id: Identifier
    query_task_id: Identifier
    requirements: Requirement
    requested_format: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")]


class Revision(Payload):
    expected_revision: Annotated[int, Field(ge=1, le=32)]
    expected_sha256: Digest


class Append(Revision):
    amendment_id: Identifier
    addition: Requirement
    updated_query_task_id: Identifier | None = None
    expected_update_sha256: Digest | None = None


class Save(Payload):
    target_vault_id: Identifier
    expected_artifact_sha256: Digest
    save_analysis: Literal[True]


class Approve(Payload):
    expected_intent_sha256: Digest


def router(config: Config) -> APIRouter:
    routes = APIRouter()

    @contextmanager
    def access(request: Request) -> Iterator[tuple[sqlite3.Connection, str]]:
        # HTTP Guard holds the main lock through authentication and response delivery.
        db = database.connect(config.state_root)
        try:
            client = auth.client(db, request.headers.get("authorization", ""))
            yield db, client["id"]
        finally:
            db.close()

    @routes.post("/v1/document-tasks")
    def submit(data: Submit, request: Request) -> dict[str, Any]:
        with access(request) as (db, client):
            return tasks.submit(
                config,
                db,
                client,
                data.submission_id,
                data.query_task_id,
                data.requirements,
                data.requested_format,
            )

    @routes.get("/v1/document-tasks/{task_id}")
    def status(task_id: str, request: Request) -> dict[str, Any]:
        with access(request) as (db, client):
            value = tasks.status(config, db, client, task_id)
            if binding.get(db, task_id) is not None:
                value["file_delivery"] = delivery.status(db, client, task_id)
            return value

    @routes.get("/v1/document-tasks/{task_id}/versions")
    def versions(task_id: str, request: Request) -> list[dict[str, Any]]:
        with access(request) as (db, client):
            return continuation.versions(config, db, client, task_id)

    @routes.get("/v1/document-tasks/{task_id}/draft")
    def draft(task_id: str, request: Request) -> Response:
        with access(request) as (db, client):
            raw, metadata = artifacts.get(config, db, client, task_id)
            return Response(
                raw,
                media_type=metadata["artifact"]["media_type"],
                headers={
                    "Content-Disposition": 'attachment; filename="'
                    + metadata["artifact"]["original_name"]
                    + '"'
                },
            )

    @routes.post("/v1/document-tasks/{task_id}/append")
    def append(task_id: str, data: Append, request: Request) -> dict[str, Any]:
        with access(request) as (db, client):
            if binding.get(db, task_id) is None:
                if (
                    data.updated_query_task_id is not None
                    or data.expected_update_sha256 is not None
                ):
                    raise InvalidInput("document_prepared_input_update_requires_new_task")
                return tasks.amend(
                    config,
                    db,
                    client,
                    task_id,
                    data.addition,
                    expected_revision=data.expected_revision,
                    expected_sha256=data.expected_sha256,
                )
            return continuation.enqueue(
                config,
                db,
                client,
                task_id,
                data.amendment_id,
                data.addition,
                expected_revision=data.expected_revision,
                expected_sha256=data.expected_sha256,
                updated_query_task_id=data.updated_query_task_id,
                expected_update_sha256=data.expected_update_sha256,
            )

    @routes.get("/v1/document-tasks/{task_id}/input-update/{query_task_id}")
    def preview_update(task_id: str, query_task_id: str, request: Request) -> dict[str, Any]:
        with access(request) as (db, client):
            return continuation.update_preview(config, db, client, task_id, query_task_id)

    @routes.get("/v1/document-candidates/{query_task_id}")
    def candidates(query_task_id: str, request: Request) -> dict[str, Any]:
        with access(request) as (db, client):
            return continuation.candidates(config, db, client, query_task_id)

    @routes.post("/v1/document-tasks/{task_id}/cancel")
    def cancel(task_id: str, data: Revision, request: Request) -> dict[str, Any]:
        with access(request) as (db, client):
            return continuation.cancel_all(
                config, db, client, task_id, data.expected_revision, data.expected_sha256
            )

    @routes.post("/v1/document-tasks/{task_id}/save")
    def save_analysis(task_id: str, data: Save, request: Request) -> dict[str, Any]:
        with access(request) as (db, client):
            prepared = save.prepare(
                config,
                db,
                client,
                task_id,
                data.target_vault_id,
                expected_artifact_sha256=data.expected_artifact_sha256,
                allow_save=True,
            )
            if prepared["state"] == "scope_approval_required":
                return prepared
            return save.publish(config, db, client, task_id, allow_save=True)

    @routes.post("/admin/document-tasks/{task_id}/save-approval")
    def approval(task_id: str, data: Approve, request: Request) -> dict[str, Any]:
        db = database.connect(config.state_root)
        try:
            auth.admin(
                db,
                request.cookies.get(auth.COOKIE, ""),
                request.headers.get("x-csrf-token", ""),
            )
            bound = binding.get(db, task_id)
            if bound is None:
                raise auth.Denied(404)
            return save.approve(
                db,
                bound["run_id"],
                data.expected_intent_sha256,
                admin_cookie=request.cookies.get(auth.COOKIE, ""),
                csrf=request.headers.get("x-csrf-token", ""),
            )
        finally:
            db.close()

    return routes
