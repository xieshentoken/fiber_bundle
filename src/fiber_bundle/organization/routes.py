"""Admin JSON boundary for package registration, policies, plans and manual corrections."""

from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import Field

from ..config import Config
from ..payloads import Identifier, Payload
from ..query_http import access
from . import jobs, manual_membership, package_discovery, packages
from .contracts import Policy
from .overview import browse, progress


class Scope(Payload):
    client_id: Identifier
    vault_id: Identifier


class Registration(Scope):
    path: str = Field(min_length=1, max_length=1000)


class PolicySave(Scope):
    expected_revision: int = Field(strict=True, ge=0)
    settings: Policy


class Submission(Scope):
    target: str = Field(default="Inbox", max_length=1000)
    preview: bool = True


class Member(Scope):
    entity_id: str = Field(max_length=100)
    moc_id: Identifier
    action: str = Field(pattern=r"^(link|unlink|unlock)$")


class Members(Scope):
    entity_id: str = Field(min_length=1, max_length=100)
    moc_ids: list[Identifier] = Field(min_length=1, max_length=1000)
    action: str = Field(pattern=r"^(link|unlink|unlock)$")


def router(config: Config) -> Any:
    routes = APIRouter(prefix="/admin/organization")

    @routes.get("/progress")
    def task_progress(client_id: str, vault_id: str, request: Request) -> Any:
        with access(config, request, admin=True) as (db, _):
            return progress(config, db, client_id, vault_id)

    @routes.get("")
    def listing(
        client_id: str,
        vault_id: str,
        request: Request,
        offset: int = Query(0, ge=0),
        q: str = Query("", max_length=200),
        exceptions: bool = True,
    ) -> Any:
        with access(config, request, admin=True) as (db, _):
            return browse(config, db, client_id, vault_id, offset, q, exceptions)

    @routes.post("/packages/scan")
    def scan_packages(data: Scope, request: Request) -> Any:
        with access(config, request, admin=True) as (db, _):
            return package_discovery.request(config, db, data.client_id, data.vault_id)

    @routes.post("/packages")
    def register(data: Registration, request: Request) -> Any:
        with access(config, request, admin=True) as (db, _):
            return packages.register(config, db, data.client_id, data.vault_id, data.path)

    @routes.put("/policy")
    def policy(data: PolicySave, request: Request) -> Any:
        with access(config, request, admin=True) as (db, _):
            return jobs.save_policy(
                config,
                db,
                data.client_id,
                data.vault_id,
                data.settings.model_dump(),
                data.expected_revision,
            )

    @routes.post("/jobs")
    def submit(data: Submission, request: Request) -> Any:
        from ..config import InvalidInput

        if data.target == "全库" and not data.preview:
            raise InvalidInput("organization_history_requires_preview")
        with access(config, request, admin=True) as (db, _):
            return jobs.submit(
                config, db, data.client_id, data.vault_id, data.target, preview=data.preview
            )

    @routes.post("/jobs/{identity}/{operation}")
    def action(identity: str, operation: str, data: Scope, request: Request) -> Any:
        with access(config, request, admin=True) as (db, _):
            return jobs.action(config, db, data.client_id, data.vault_id, identity, operation)

    @routes.post("/members")
    def member(data: Member, request: Request) -> Any:
        with access(config, request, admin=True) as (db, _):
            return manual_membership.apply(
                config,
                db,
                data.client_id,
                data.vault_id,
                data.entity_id,
                [data.moc_id],
                data.action,
            )

    @routes.post("/members/batch")
    def members(data: Members, request: Request) -> Any:
        with access(config, request, admin=True) as (db, _):
            return manual_membership.apply(
                config, db, data.client_id, data.vault_id, data.entity_id, data.moc_ids, data.action
            )

    from .maintenance_routes import register as register_maintenance

    register_maintenance(routes, config)
    return routes
