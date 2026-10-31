"""Admin scope/CSRF boundary for maintenance previews and explicit confirmation."""

from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import Field

from ..payloads import Identifier, Payload
from ..query_http import access
from . import jobs, maintenance
from .maintenance_plan import NewMoc


class Submission(Payload):
    client_id: Identifier
    vault_id: Identifier
    mode: Literal["quick", "additive", "deep"]
    new_mocs: list[NewMoc] = Field(default_factory=list, max_length=100)
    unavailable_action: Literal["moc_only", "defer_links"] = "moc_only"


class Repair(Payload):
    client_id: Identifier
    vault_id: Identifier
    operation_id: Identifier
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Action(Payload):
    client_id: Identifier
    vault_id: Identifier
    plan_sha256: str | None = None


def register(routes: APIRouter, config: Any) -> None:
    @routes.post("/maintenance")
    def submit(data: Submission, request: Request) -> Any:
        with access(config, request, admin=True) as (db, _):
            return maintenance.public(
                maintenance.submit(
                    config,
                    db,
                    data.client_id,
                    data.vault_id,
                    data.mode,
                    [m.model_dump() for m in data.new_mocs],
                    unavailable_action=data.unavailable_action,
                ),
                config=config,
                db=db,
            )

    @routes.post("/maintenance/{identity}/links/preview")
    def repair_preview(identity: str, data: Action, request: Request) -> Any:
        from .maintenance_link_repair import preview

        with access(config, request, admin=True) as (db, _):
            return preview(
                config, db, jobs.get(config, db, data.client_id, data.vault_id, identity)
            )

    @routes.post("/maintenance/{identity}/links/confirm")
    def repair_confirm(identity: str, data: Repair, request: Request) -> Any:
        from .maintenance_link_repair import apply

        with access(config, request, admin=True) as (db, _):
            job = jobs.get(config, db, data.client_id, data.vault_id, identity)
            return apply(config, db, job, data.operation_id, data.plan_sha256)

    @routes.post("/maintenance/{identity}/{operation}")
    def action(identity: str, operation: str, data: Action, request: Request) -> Any:
        with access(config, request, admin=True) as (db, _):
            job = jobs.get(config, db, data.client_id, data.vault_id, identity)
            return maintenance.public(
                maintenance.action(config, db, job, operation, data.plan_sha256),
                config=config,
                db=db,
            )
