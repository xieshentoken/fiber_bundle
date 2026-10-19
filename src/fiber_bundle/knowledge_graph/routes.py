"""Admin JSON boundary; host modules own graph state, workers and MOC behavior."""

from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import Field, field_validator

from ..config import Config
from ..payloads import Identifier, Payload
from ..query_http import Digest, PolicySave, access
from . import catalog, intake, jobs, launch, policy, store, writeback


class Scope(Payload):
    client_id: Identifier
    vault_id: Identifier


class Selection(Payload):
    note_id: Annotated[str, Field(min_length=41, max_length=48)]
    expected_sha256: Digest


class Submission(Scope):
    selected: Annotated[list[Selection], Field(min_length=1, max_length=100)]


class Review(Scope):
    job_id: Identifier | None = None
    configuration: Digest
    revision: Annotated[int, Field(strict=True, ge=0)]
    state: Literal["accepted", "rejected", "suggested"]
    reason: Annotated[str, Field(min_length=1, max_length=400)]


class MocIntent(Scope):
    object_id: Digest
    moc_id: Identifier
    revision: Annotated[int, Field(strict=True, ge=0)]
    relative: Annotated[str, Field(min_length=1, max_length=512)]
    expected_sha256: Annotated[str, Field(pattern=r"^(absent|[a-f0-9]{64})$")]
    undo: Digest | None = None


class Confirmation(Scope):
    confirm: Literal[True]

    @field_validator("confirm", mode="before")
    @classmethod
    def explicit(cls, value: object) -> object:
        if value is not True:
            raise ValueError("explicit confirmation required")
        return value


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/admin/knowledge-graph")

    from .runtime_routes import router as runtime_router

    routes.include_router(runtime_router(config))

    # Guard serializes authentication and delivery. Only an explicitly launched CLI infers.
    @routes.get("/policy")
    def inspect(request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            profile = policy.current(config)
            return {
                "configured": profile is not None,
                "enabled": bool(profile and profile.enabled),
                "configuration": profile.digest if profile else None,
                "value": asdict(profile) if profile else None,
            }

    @routes.put("/policy")
    def save(data: PolicySave, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            profile = policy.parse(data.value)
            policy.save(config, profile, data.expected_sha256)
            return inspect(request)

    @routes.get("/notes")
    def notes(
        client_id: str,
        vault_id: str,
        request: Request,
        q: str = Query(default="", max_length=200),
        offset: int = Query(default=0, ge=0, le=10000),
    ) -> list[dict[str, Any]]:
        from .note_listing import listing

        with access(config, request, admin=True) as (db, _):
            return listing(config, db, client_id, vault_id, q, offset)

    @routes.get("/suggestions")
    def suggestions(client_id: str, vault_id: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return catalog.browse(config, client_id, vault_id, _locked=True)

    @routes.get("/jobs")
    def listing(client_id: str, vault_id: str, request: Request) -> list[dict[str, Any]]:
        with access(config, request, admin=True):
            return jobs.status(config, client_id, vault_id, _locked=True)

    @routes.post("/jobs", status_code=202)
    def submit(data: Submission, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return jobs.submit(
                config,
                data.client_id,
                data.vault_id,
                tuple(intake.Selection(x.note_id, x.expected_sha256) for x in data.selected),
                _locked=True,
            )

    @routes.post("/jobs/{jid}/start", status_code=202)
    def start(jid: str, data: Scope, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return launch.launch(config, data.client_id, data.vault_id, jid)

    @routes.post("/jobs/{jid}/{action}")
    def job_action(
        jid: str, action: Literal["cancel", "quarantine"], data: Scope, request: Request
    ) -> dict[str, Any]:
        with access(config, request, admin=True):
            work = jobs.cancel if action == "cancel" else jobs.quarantine
            return work(config, data.client_id, data.vault_id, jid, _locked=True)

    @routes.post("/suggestions/{oid}/review")
    def review(oid: str, data: Review, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return catalog.review(
                config,
                data.client_id,
                data.vault_id,
                oid,
                configuration=data.configuration,
                revision=data.revision,
                state=data.state,
                reason=data.reason,
                job_id=data.job_id,
                _locked=True,
            )

    @routes.get("/writebacks")
    def intents(client_id: str, vault_id: str, request: Request) -> list[dict[str, Any]]:
        with access(config, request, admin=True):
            jobs.authorize(config, client_id, vault_id)
            with store.connection(config) as db:
                return (
                    [
                        writeback._row(config, client_id, vault_id, r[0])
                        for r in db.execute(
                            "SELECT id FROM writebacks WHERE client_id=? AND vault_id=? LIMIT 100",
                            (client_id, vault_id),
                        )
                    ]
                    if db
                    else []
                )

    @routes.post("/writebacks")
    def preview(data: MocIntent, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return writeback.prepare(
                config,
                data.client_id,
                data.vault_id,
                data.object_id,
                moc_id=data.moc_id,
                revision=data.revision,
                relative=data.relative,
                expected_sha256=data.expected_sha256,
                undo=data.undo,
                _locked=True,
            )

    @routes.post("/writebacks/{wid}/apply")
    def apply(wid: str, data: Confirmation, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return writeback.apply(
                config, data.client_id, data.vault_id, wid, confirm=data.confirm, _locked=True
            )

    @routes.post("/writebacks/{wid}/{action}")
    def reconcile(
        wid: str, action: Literal["cancel", "reconcile"], data: Scope, request: Request
    ) -> dict[str, Any]:
        with access(config, request, admin=True):
            work = writeback.cancel if action == "cancel" else writeback.reconcile
            return work(config, data.client_id, data.vault_id, wid, _locked=True)

    return routes
