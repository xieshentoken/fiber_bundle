"""Authenticated policy and exception queue endpoints. No caller-supplied system assessments."""

from typing import Annotated, Any

from fastapi import APIRouter, Request
from pydantic import Field, StrictBool

from . import automatic_jobs, automatic_policy
from .config import Config, InvalidInput
from .payloads import Payload
from .query_http import access


class Update(Payload):
    expected_revision: Annotated[int, Field(strict=True, ge=0)]
    settings: automatic_policy.Policy


class Retry(Payload):
    approve_new_model_attempt: StrictBool = False


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/admin/vault-catalog")

    @routes.get("/{vault_id}/automatic-policy")
    def policy(vault_id: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            from .agent_connections.models import available

            return {**automatic_policy.get(db, vault_id), "models": available(config)}

    @routes.put("/{vault_id}/automatic-policy")
    def save(vault_id: str, data: Update, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return automatic_policy.save(
                config, db, vault_id, data.expected_revision, data.settings
            )

    @routes.get("/{vault_id}/automatic-jobs")
    def jobs(vault_id: str, request: Request) -> list[dict[str, Any]]:
        with access(config, request, admin=True) as (db, _):
            return automatic_jobs.listing(db, vault_id)

    @routes.post("/{vault_id}/automatic-jobs/{identity}/cancel")
    def cancel(vault_id: str, identity: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            job = automatic_jobs.get(db, identity)
            if job["vault_id"] != vault_id or job["state"] == "complete":
                raise InvalidInput("automatic_job_cannot_cancel")
            automatic_jobs.settle(db, job, "cancelled")
            return {"state": "cancelled", "completed_effects_preserved": True}

    @routes.post("/{vault_id}/automatic-jobs/{identity}/publish")
    def publish(vault_id: str, identity: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            job = automatic_jobs.get(db, identity)
            if job["vault_id"] != vault_id or job["state"] != "reviewed":
                raise InvalidInput("automatic_review_not_ready")
            automatic_jobs.authorize(config, db, job)
            automatic_jobs.settle(db, job, "queued", "publish")
            return {"state": "queued"}

    @routes.post("/{vault_id}/automatic-jobs/{identity}/reconcile")
    def reconcile(
        vault_id: str, identity: str, request: Request, data: Retry | None = None
    ) -> dict[str, Any]:
        from .automatic_recovery import resume

        with access(config, request, admin=True) as (db, _):
            return resume(
                config,
                db,
                vault_id,
                identity,
                approve_new_model_attempt=bool(data and data.approve_new_model_attempt),
            )

    @routes.get("/{vault_id}/attachment-projections")
    def projections(vault_id: str, request: Request) -> list[dict[str, Any]]:
        from .attachment_recovery import listing

        with access(config, request, admin=True) as (db, _):
            return listing(db, vault_id)

    @routes.post("/{vault_id}/attachment-projections/{identity}/reconcile")
    def projection_resume(vault_id: str, identity: str, request: Request) -> dict[str, Any]:
        from .attachment_recovery import resume

        with access(config, request, admin=True) as (db, _):
            return resume(config, db, vault_id, identity)

    return routes
