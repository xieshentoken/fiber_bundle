"""Admin-only saved-source selection, task admission, review and explicit execution/publish."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import Field, field_validator

from . import article_reviews, source_processing_policy, source_selection, source_tasks
from .config import Config
from .execution_config import ExecutionConfig
from .payloads import Identifier, Payload
from .query_http import Digest, PolicySave, access


class SourceRequest(Payload):
    client_id: Identifier
    vault_id: Identifier
    receipt_id: Identifier
    asset_id: Identifier
    source_sha256: Digest
    kind: Literal["document", "article"]
    submission_id: Identifier
    configuration_sha256: Digest
    model_configuration_sha256: Digest


class ArticleAssessment(Payload):
    manifest_sha256: Digest
    source_reviewed: Literal[True]
    text_reviewed: Literal[True]
    images_reviewed: Literal[True]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]

    @field_validator("source_reviewed", "text_reviewed", "images_reviewed", mode="before")
    @classmethod
    def explicit(cls, value: object) -> object:
        if value is not True:
            raise ValueError("explicit review required")
        return value


class ArticlePublication(Payload):
    review_id: Identifier
    manifest_sha256: Digest
    confirm: Literal[True]

    @field_validator("confirm", mode="before")
    @classmethod
    def explicit(cls, value: object) -> object:
        if value is not True:
            raise ValueError("explicit publication required")
        return value


def router(config: Config, execution: ExecutionConfig | None) -> APIRouter:
    routes = APIRouter(prefix="/admin")
    from .project_skill_routes import router as skill_router

    routes.include_router(skill_router(config))

    @routes.get("/sources")
    def sources(client_id: str, vault_id: str, request: Request) -> list[dict[str, Any]]:
        with access(config, request, admin=True) as (db, _):
            return source_selection.listing(config, db, client_id, vault_id)

    @routes.get("/source-policy")
    def policy(request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return source_processing_policy.inspect(config, execution)

    @routes.put("/source-policy")
    def save_policy(data: PolicySave, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return source_processing_policy.save(
                config, execution, data.value, data.expected_sha256
            )

    @routes.post("/source-tasks", status_code=202)
    def submit(data: SourceRequest, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return source_tasks.submit(config, db, execution, data.model_dump())

    @routes.post("/source-tasks/{run_id}/start", status_code=202)
    def start(run_id: str, request: Request) -> dict[str, str]:
        with access(config, request, admin=True) as (db, _):
            return source_tasks.launch(config, db, execution, run_id)

    @routes.get("/articles")
    def articles(request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return article_reviews.listing(db)

    @routes.get("/articles/{run_id}/preview")
    def preview(run_id: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return article_reviews.packet(config, db, run_id)

    @routes.get("/articles/{run_id}/source")
    def source(run_id: str, request: Request) -> Response:
        with access(config, request, admin=True) as (db, _):
            _, _, verified = article_reviews.capture(config, db, run_id)
            from .download_names import disposition

            return Response(
                verified.raw_html,
                media_type="text/html",
                headers={"Content-Disposition": disposition("source.html")},
            )

    @routes.get("/articles/{run_id}/images/{sha256}")
    def image(run_id: str, sha256: str, request: Request) -> Response:
        with access(config, request, admin=True) as (db, _):
            _, _, verified = article_reviews.capture(config, db, run_id)
            for image in verified.draft.images:
                if image.sha256 == sha256:
                    return Response(image.data, media_type=image.media_type)
            from .auth import Denied

            raise Denied(404)

    @routes.post("/articles/{run_id}/reviews")
    def review(run_id: str, data: ArticleAssessment, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return article_reviews.record(config, db, run_id, data.model_dump())

    @routes.post("/articles/{run_id}/publish")
    def publish(run_id: str, data: ArticlePublication, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return article_reviews.publish(config, db, run_id, data.review_id, data.manifest_sha256)

    return routes
