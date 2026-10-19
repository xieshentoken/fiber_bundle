"""GUI administration and identity-bound transport; all business lives in feature modules."""

import sqlite3
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import Field, model_validator

from ..config import Config, InvalidInput
from ..payloads import Identifier, Payload
from ..query_http import access
from . import application, configuration, evidence_verification, files, installation, models, store
from .contracts import ConnectionContext, ConnectionSpec, TurnRequest
from .runtime import Runtime


class Change(Payload):
    request_id: Identifier
    expected_revision: Annotated[int, Field(ge=1)]


class Create(Payload):
    request_id: Identifier
    configuration: ConnectionSpec


class Save(Change):
    configuration: ConnectionSpec


class Operation(Change):
    kind: Literal["apply_config", "probe", "test_query", "rotate_credential"]
    request: TurnRequest | None = None

    @model_validator(mode="after")
    def request_kind(self) -> "Operation":
        if (self.kind == "test_query") != (self.request is not None):
            raise ValueError("agent_operation_payload_invalid")
        return self


class Verify(Payload):
    context: ConnectionContext
    sources: Annotated[list[dict[str, Any]], Field(max_length=10)]


def owned(db: sqlite3.Connection, cid: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM agent_connections WHERE client_id=?", (cid,)).fetchone()
    if not isinstance(row, sqlite3.Row):
        raise InvalidInput("agent_connection_unavailable")
    return row


def result(config: Config, db: sqlite3.Connection, identity: str, oid: str) -> dict[str, Any]:
    operation = store.op(db, identity, oid)
    view = store.op_view(operation)
    if operation["kind"] in ("probe", "test_query") and operation["state"] == "succeeded":
        summary = view["result"]
        if summary["context"] != store.context(config, db, identity):
            view["result"] = {"state": "outdated"}
            return view
        if operation["kind"] == "test_query":
            try:
                document = files.private_json(
                    files.folder(config, identity) / (oid + ".answer.json"), 262144
                )
                if not isinstance(document, dict):
                    raise ValueError()
                evidence_verification.verify(
                    config,
                    db,
                    store.get(db, identity)["client_id"],
                    document["context"],
                    document["answer"]["sources"],
                )
                answer = document["answer"]
                question = answer.get("question")
                if isinstance(question, str) and question.strip():
                    from ..expert_answers import enrich

                    answer = enrich(
                        config, db, store.get(db, identity)["client_id"], question, answer
                    )
                view["answer"] = answer
            except Exception:
                view["result"] = {"state": "outdated"}
    return view


def router(config: Config, runtime: Runtime) -> APIRouter:
    routes = APIRouter()
    prefix = "/admin/agent-connections"

    @routes.get(prefix + "/catalog")
    def catalog(request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            try:
                info = installation.current()
                hermes = {k: info[k] for k in ("state", "version", "source_sha256", "root")}
            except (OSError, InvalidInput):
                hermes = {"state": "unavailable"}
            return {"hermes": hermes, "models": models.available(config)}

    @routes.get(prefix)
    def listing(request: Request) -> list[dict[str, Any]]:
        with access(config, request, admin=True) as (db, _):
            return [
                store.public(config, db, row)
                for row in db.execute(
                    "SELECT * FROM agent_connections ORDER BY created_at DESC LIMIT 50"
                )
            ]

    @routes.post(prefix, status_code=201)
    def create(data: Create, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return configuration.create(config, db, data.configuration, data.request_id)

    @routes.get(prefix + "/{identity}")
    def detail(identity: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return store.public(config, db, store.get(db, identity))

    @routes.patch(prefix + "/{identity}")
    def save(identity: str, data: Save, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return configuration.save(
                config, db, identity, data.configuration, data.expected_revision, data.request_id
            )

    @routes.get(prefix + "/{identity}/configuration-export")
    def export(identity: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            row = store.get(db, identity)
            if not row["applied_json"]:
                raise store.Conflict("agent_apply_required")
            return application.exported(config, row)

    @routes.post(prefix + "/{identity}/operations", status_code=202)
    def start(identity: str, data: Operation, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return runtime.start(
                db,
                identity,
                data.kind,
                data.request_id,
                data.expected_revision,
                data.request.model_dump() if data.request else None,
            )

    @routes.get(prefix + "/{identity}/operations")
    def history(
        identity: str, request: Request, offset: int = Query(default=0, ge=0, le=10000)
    ) -> list[dict[str, Any]]:
        with access(config, request, admin=True) as (db, _):
            store.get(db, identity)
            return [
                store.op_view(row)
                for row in db.execute(
                    "SELECT * FROM agent_connection_operations WHERE connection_id=? "
                    "ORDER BY created_at DESC,id DESC LIMIT 50 OFFSET ?",
                    (identity, offset),
                )
            ]

    @routes.get(prefix + "/{identity}/operations/{oid}")
    def status(identity: str, oid: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return result(config, db, identity, oid)

    @routes.post(prefix + "/{identity}/operations/{oid}/cancel")
    def cancel(identity: str, oid: str, data: Change, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return configuration.cancel(db, identity, oid, data.expected_revision, data.request_id)

    @routes.post(prefix + "/{identity}/{action}")
    def control(
        identity: str, action: Literal["enable", "pause", "revoke"], data: Change, request: Request
    ) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return configuration.control(
                config, db, identity, action, data.expected_revision, data.request_id
            )

    @routes.get("/v1/agent-context")
    def context(request: Request) -> dict[str, Any]:
        with access(config, request) as (db, cid):
            assert cid is not None
            return store.context(config, db, owned(db, cid)["id"])

    @routes.post("/v1/agent-evidence/verify")
    def verify(data: Verify, request: Request) -> dict[str, bool]:
        with access(config, request) as (db, cid):
            assert cid is not None
            return evidence_verification.verify(
                config, db, cid, data.context.model_dump(), data.sources
            )

    @routes.post("/v1/agent-requests", status_code=202)
    def managed_request(data: TurnRequest, request: Request) -> dict[str, Any]:
        with access(config, request) as (db, cid):
            assert cid is not None
            row = owned(db, cid)
            return runtime.start(
                db, row["id"], "test_query", data.request_id, row["revision"], data.model_dump()
            )

    @routes.get("/v1/agent-requests/{oid}")
    def managed_status(oid: str, request: Request) -> dict[str, Any]:
        with access(config, request) as (db, cid):
            assert cid is not None
            return result(config, db, owned(db, cid)["id"], oid)

    return routes
