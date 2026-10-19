"""Administrator-only source/frames review and explicit media publication; no bearer approval."""

import base64
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

from fastapi import APIRouter, Request, Response
from pydantic import Field

from . import (
    auth,
    database,
    media_admin_store,
    media_bindings,
    media_jobs,
    media_processing,
    media_publication,
    media_review,
    media_review_source,
)
from .config import Config
from .media_review import Assessment
from .payloads import Payload


class Cleanup(Payload):
    confirmation_sha256: Annotated[str, Field(pattern="^[a-f0-9]{64}$")]


class Publication(Payload):
    review_id: Annotated[str, Field(pattern="^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$")]


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/admin/media")

    @contextmanager
    def access(request: Request) -> Iterator[sqlite3.Connection]:
        db = database.connect(config.state_root)
        try:
            auth.admin(
                db,
                request.cookies.get(auth.COOKIE, ""),
                request.headers.get("x-csrf-token", "") if request.method != "GET" else None,
            )
            yield db
        finally:
            db.close()

    def packet(db: sqlite3.Connection, job: str) -> tuple[sqlite3.Row, dict[str, Any]]:
        row = media_admin_store.task(db, job)
        auth.grant(db, row["client_id"], row["vault_id"], "original")
        return row, media_processing.result_locked(config, db, row["client_id"], job)

    @routes.get("")
    def listing(request: Request) -> dict[str, Any]:
        with access(request) as db:
            return media_admin_store.listing(db)

    @routes.get("/{job}/review-packet")
    def review_packet(job: str, request: Request) -> dict[str, Any]:
        with access(request) as db:
            row, value = packet(db, job)
            review = media_review.saved(db, job, value)
            publication = media_admin_store.publication(db, job)
            return {
                "candidate": value,
                "units": media_review.units(value),
                "mode": media_jobs.binding(row)["request"]["evidence_mode"],
                "source_context": media_review_source.context(db, row, value),
                "review": review,
                "publication": publication,
                "review_gate": media_review.gate(value, review["assessment"] if review else None),
            }

    @routes.get("/{job}/source")
    def source(job: str, request: Request) -> Response:
        with access(request) as db:
            row, _ = packet(db, job)
            bound = media_jobs.binding(row)
            data, _ = media_bindings.authorize(
                config, db, row["client_id"], bound, permission="original"
            )
            return Response(
                data,
                media_type=bound["source"]["media_type"],
                headers={
                    "Content-Disposition": 'attachment; filename="source.'
                    + bound["source"]["format"]
                    + '"'
                },
            )

    @routes.get("/{job}/frames/{sha}")
    def frame(job: str, sha: str, request: Request) -> Response:
        with access(request) as db:
            _, value = packet(db, job)
            for f in value["frames"]:
                if f["sha256"] == sha:
                    return Response(
                        base64.b64decode(f["data"], validate=True), media_type="image/jpeg"
                    )
            raise auth.Denied(404)

    @routes.post("/{job}/review")
    def review(job: str, data: Assessment, request: Request) -> dict[str, Any]:
        with access(request) as db:
            _, value = packet(db, job)
            return media_review.record(db, job, value, data.model_dump())

    @routes.post("/{job}/publish")
    def publish(job: str, request: Request, data: Publication | None = None) -> dict[str, Any]:
        with access(request) as db:
            packet(db, job)
            return media_publication.publish_locked(
                config, db, job, review_id=data.review_id if data else None
            )

    @routes.post("/{job}/cleanup-plan")
    def cleanup_plan(job: str, request: Request) -> dict[str, Any]:
        from . import media_cleanup

        with access(request) as db:
            return media_cleanup.prepare_locked(config, db, job)

    @routes.post("/{job}/cleanup")
    def cleanup(job: str, data: Cleanup, request: Request) -> dict[str, Any]:
        from . import media_cleanup

        with access(request) as db:
            return media_cleanup.apply_locked(config, db, job, data.confirmation_sha256)

    return routes
