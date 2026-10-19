"""Authenticated GUI transport for private channel setup, lifecycle and shared test queries."""

from typing import Annotated, Any

from fastapi import APIRouter, Request, Response
from pydantic import Field

from .. import auth
from ..config import Config, InvalidInput
from ..payloads import Payload
from ..query_http import access
from ..query_routing_files import read
from . import configuration, control, health, intake_routes, lifecycle_routes, query, store


class PairConfirm(Payload):
    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    owner_id: Annotated[str, Field(min_length=1, max_length=400)]


class Test(Payload):
    question: Annotated[str, Field(min_length=1, max_length=2000)]
    execute_model: bool = False


class Reconcile(Payload):
    event_id: Annotated[str, Field(min_length=1, max_length=400)]
    acknowledgement: Annotated[str, Field(pattern=r"^(sent|not_sent|unknown)$")]


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/admin/channels")
    from .expert_routes import router as expert_router

    routes.include_router(expert_router(config))
    routes.include_router(lifecycle_routes.router(config))
    routes.include_router(intake_routes.router(config))
    from .services.routes import router as service_router

    routes.include_router(service_router(config))

    @routes.get("/overview")
    def overview(request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (main, _):
            return health.overview(config, main)

    @routes.get("")
    def listing(request: Request) -> list[dict[str, Any]]:
        with access(config, request, admin=True), store.readonly(config) as db:
            if db is None:
                return []
            return [
                configuration.public(config, store.get(db, row[0]))
                for row in db.execute(
                    "SELECT id FROM channels WHERE state!='removed' ORDER BY rowid LIMIT 50"
                )
            ]

    @routes.post("", status_code=201)
    def save(data: configuration.Save, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (main, _):
            return configuration.save(config, main, data)

    @routes.post("/{identity}/check", status_code=202)
    def check(identity: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (main, _):
            return control.launch(config, main, identity, "check")

    @routes.post("/{identity}/pair", status_code=202)
    def pair(identity: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (main, _):
            return control.launch(config, main, identity, "pair")

    @routes.post("/{identity}/login", status_code=202)
    def login(identity: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (main, _):
            return control.launch(config, main, identity, "login")

    @routes.post("/{identity}/confirm-owner")
    def confirm(identity: str, data: PairConfirm, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return control.confirm_owner(config, identity, data.expected_revision, data.owner_id)

    @routes.get("/{identity}/start-readiness")
    def readiness(identity: str, request: Request) -> dict[str, Any]:
        from . import intake_repository, operations
        from .validation import start_issues

        with access(config, request, admin=True) as (main, _), store.readonly(config) as db:
            if db is None:
                raise auth.Denied(404)
            channel = store.get(db, identity)
            store.authorize(config, main, channel)
            if operations.pending(db, identity):
                raise InvalidInput("channel_operation_pending")
            if not channel["owner"]:
                raise InvalidInput("channel_pairing_required")
            if control.status(config, identity).get("instance_id"):
                raise InvalidInput("channel_instance_already_running")
            if channel["state"] not in {"paused", "unpaired", "error"}:
                raise InvalidInput("channel_pause_required")
            intake = intake_repository.policy(db, identity)["settings"]
            issues = start_issues(channel["value"], intake_enabled=intake["enabled"])
            if db.execute(
                "SELECT 1 FROM events WHERE channel_id=? AND state IN ('sending','unknown')",
                (identity,),
            ).fetchone():
                raise InvalidInput("channel_delivery_requires_reconciliation")
            return {"ready": not issues, "issues": issues, "revision": channel["revision"]}

    @routes.post("/{identity}/start", status_code=202)
    def start(identity: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (main, _):
            return control.launch(config, main, identity, "run")

    @routes.post("/{identity}/pause")
    def pause(identity: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (main, _):
            return control.pause(config, main, identity)

    @routes.post("/{identity}/test")
    def test(identity: str, data: Test, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (main, _):
            return query.test(config, main, identity, data.question, data.execute_model)

    @routes.get("/{identity}/qr")
    def qr(identity: str, request: Request) -> Response:
        with access(config, request, admin=True), store.readonly(config) as db:
            if db is None:
                raise auth.Denied(404)
            channel = store.get(db, identity)
            if channel["value"]["platform"] != "weixin" or channel["state"] != "login":
                raise InvalidInput("channel_qr_unavailable")
            return Response(
                read(store.home(config, identity) / "login-qr.png", 262144), media_type="image/png"
            )

    @routes.get("/{identity}/events")
    def events(identity: str, request: Request) -> list[dict[str, Any]]:
        from .delivery_parts import public

        with access(config, request, admin=True), store.readonly(config) as db:
            if db is None:
                raise auth.Denied(404)
            store.get(db, identity)
            return [
                {k: row[k] for k in ("event_id", "revision", "state", "created")}
                | {"delivery_parts": public(row["value"])}
                for row in db.execute(
                    "SELECT event_id,revision,state,created,value FROM events "
                    "WHERE channel_id=? ORDER BY created DESC LIMIT 50",
                    (identity,),
                )
            ]

    @routes.post("/{identity}/reconcile")
    def reconcile(identity: str, data: Reconcile, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True), store.opened(config) as db:
            channel = store.get(db, identity)
            if channel["state"] not in {
                "paused",
                "error",
                "unpaired",
                "stopping",
                "unbinding",
                "removing",
                "removed",
            }:
                raise InvalidInput("channel_pause_required")
            if control.status(config, identity)["process_state"] != "stopped":
                raise InvalidInput("channel_process_not_stopped")
            updated = db.execute(
                "UPDATE events SET state=? WHERE channel_id=? AND event_id=? "
                "AND state IN ('unknown','sending')",
                (
                    "sent"
                    if data.acknowledgement == "sent"
                    else "not_sent"
                    if data.acknowledgement == "not_sent"
                    else "unknown",
                    identity,
                    data.event_id,
                ),
            ).rowcount
            if not updated:
                raise InvalidInput("channel_reconcile_event_unavailable")
            return {
                "event_id": data.event_id,
                "acknowledgement": data.acknowledgement,
                "replay": False,
            }

    return routes
