"""Authenticated source-specific preflight; no new parsing or review rules in the router."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request
from pydantic import Field, StrictBool

from . import (
    article_reviews,
    document_publication,
    expert_publication,
    media_publication,
    publication_layout,
)
from .config import Config, InvalidInput
from .payloads import Identifier, Payload
from .query_http import access


class Preview(Payload):
    review_id: Identifier | None = None
    manifest_sha256: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$")] = None
    artifact_sha256: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$")] = None
    client_id: Identifier | None = None
    source_reviewed: StrictBool = False
    candidate_acknowledged: StrictBool = False
    confirm: StrictBool = False
    batch_id: Identifier | None = None
    index: Annotated[int | None, Field(ge=0, le=1000)] = None


Kind = Literal["article", "document", "media", "expert", "intake"]


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/admin/vault-publications")

    @routes.post("/{kind}/{identity}/preview")
    def preview(
        kind: Kind, identity: Identifier, data: Preview, request: Request
    ) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            if kind == "article":
                if data.review_id is None or data.manifest_sha256 is None:
                    raise InvalidInput("layout_review_required")
                return article_reviews.publish(
                    config, db, identity, data.review_id, data.manifest_sha256, preview_only=True
                )
            if kind == "document":
                return document_publication.publish_locked(config, db, identity, preview_only=True)
            if kind == "media":
                return media_publication.publish_locked(
                    config, db, identity, review_id=data.review_id, preview_only=True
                )
            if kind == "expert":
                if data.client_id is None or data.artifact_sha256 is None:
                    raise InvalidInput("layout_review_required")
                return expert_publication.publish(
                    config,
                    db,
                    data.client_id,
                    identity,
                    data.artifact_sha256,
                    data.source_reviewed,
                    data.candidate_acknowledged,
                    preview_only=True,
                )
            from .channels import intake_reviews

            if data.batch_id is None or data.index is None or data.review_id is None:
                raise InvalidInput("layout_review_required")
            return intake_reviews.publish(
                config,
                identity,
                data.batch_id,
                data.index,
                data.review_id,
                leased=True,
                preview_only=True,
            )

    @routes.post("/{kind}/{identity}/discard-preview")
    def discard(kind: Kind, identity: Identifier, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return publication_layout.discard_preview(config, db, kind, identity)

    return routes
