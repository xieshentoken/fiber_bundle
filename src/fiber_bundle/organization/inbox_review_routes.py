"""Admin session/CSRF transport for explicitly confirmed Inbox file operations."""

from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import Field
from starlette.responses import JSONResponse

from .. import auth
from ..config import StorageError
from ..payloads import Identifier, Payload
from ..query_http import access
from . import inbox_review, inbox_review_execution, inbox_review_plan


class Scope(Payload):
    client_id: Identifier
    vault_id: Identifier


class Inspection(Scope):
    job_id: Identifier
    path: str = Field(min_length=1, max_length=1000)


class Preview(Inspection):
    action: Literal["quarantine_empty", "unlink_missing"]
    link_ids: list[str] = Field(default_factory=list, max_length=100)


class Apply(Scope):
    expected_plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def register(routes: APIRouter, config: Any) -> None:
    def invoke(request: Request, function: Any, *args: Any) -> Any:
        try:
            with access(config, request, admin=True) as (db, _):
                return function(config, db, *args)
        except auth.Denied:
            raise  # Never disclose a filename or diagnostic for a denied request.
        except (StorageError, OSError, UnicodeError) as error:
            reason = str(error)
            next_step = (
                "restore_preview"
                if reason
                in {
                    "inbox_review_concurrent_edit_preserved",
                    "inbox_review_concurrent_edit_restore_required",
                }
                else "resolve_conflict"
                if reason
                in {
                    "inbox_review_restore_path_occupied",
                    "inbox_review_move_conflict",
                }
                else "reinspect"
            )
            return JSONResponse(
                {
                    "error": reason,
                    "error_detail": {
                        "reason": reason,
                        "next_step": next_step,
                    },
                },
                status_code=409,
            )

    @routes.post("/inbox-review/inspect")
    def inspect(data: Inspection, request: Request) -> Any:
        return invoke(
            request, inbox_review.inspect, data.client_id, data.vault_id, data.job_id, data.path
        )

    @routes.post("/inbox-review/preview")
    def preview(data: Preview, request: Request) -> Any:
        return invoke(
            request,
            inbox_review_plan.preview,
            data.client_id,
            data.vault_id,
            data.job_id,
            data.path,
            data.action,
            data.link_ids,
        )

    @routes.post("/inbox-review/operations/{identity}/apply")
    def apply(identity: Identifier, data: Apply, request: Request) -> Any:
        return invoke(
            request,
            inbox_review_execution.apply,
            data.client_id,
            data.vault_id,
            identity,
            data.expected_plan_sha256,
        )

    @routes.get("/inbox-review/history")
    def history(request: Request, client_id: Identifier, vault_id: Identifier) -> Any:
        return invoke(request, inbox_review_plan.history, client_id, vault_id)

    @routes.post("/inbox-review/operations/{identity}/restore-preview")
    def restore(identity: Identifier, data: Scope, request: Request) -> Any:
        return invoke(
            request, inbox_review_plan.restore_preview, data.client_id, data.vault_id, identity
        )
