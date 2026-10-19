"""Local administrator review and explicit publication; client tokens cannot approve."""

import base64
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from . import (
    auth,
    database,
    document_admin_store,
    document_bindings,
    document_evidence,
    document_publication,
    document_review,
)
from .config import Config
from .document_review import Assessment


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/admin/documents")

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

    def packet(
        db: sqlite3.Connection, run_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], sqlite3.Row]:
        run = document_admin_store.run(db, run_id)
        # Review requires source access in addition to derived-text query access.
        auth.grant(db, run["client_id"], run["vault_id"], "original")
        metadata, candidate = document_evidence.load(config, db, run["client_id"], run_id)
        return metadata, candidate, run

    @routes.get("")
    def listing(request: Request) -> dict[str, Any]:
        with access(request) as db:
            return document_admin_store.listing(db)

    @routes.get("/{run_id}/review-packet")
    def review_packet(run_id: str, request: Request) -> dict[str, Any]:
        with access(request) as db:
            metadata, candidate, _ = packet(db, run_id)
            return {**metadata, "units": document_review.units(candidate), "candidate": candidate}

    @routes.get("/{run_id}/source")
    def source(run_id: str, request: Request) -> Response:
        with database.locked(config), access(request) as db:
            _, _, run = packet(db, run_id)
            task = document_bindings.get(db, run_id)
            assert task is not None
            bound = document_bindings.authorize(config, db, run, task, permission="query")
            mime = document_admin_store.source_media_type(db, run["version_id"], task["asset_id"])
            from .document_delivery import chunks

            return StreamingResponse(
                chunks(
                    config,
                    run_id,
                    run["client_id"],
                    bound.file,
                    request.cookies.get(auth.COOKIE, ""),
                ),
                media_type=mime,
                headers={
                    "Content-Disposition": 'attachment; filename="source.' + bound.format + '"',
                    "Content-Length": str(bound.byte_count),
                },
            )

    @routes.get("/{run_id}/images/{sha256}")
    def image(run_id: str, sha256: str, request: Request) -> Response:
        with access(request) as db:
            _, candidate, _ = packet(db, run_id)
            for item in candidate.get("attachments", []):
                if item["sha256"] == sha256:
                    return Response(
                        base64.b64decode(item["data"], validate=True), media_type=item["media_type"]
                    )
            raise auth.Denied(404)

    @routes.post("/{run_id}/reviews")
    def assess(run_id: str, data: Assessment, request: Request) -> dict[str, Any]:
        with access(request) as db:
            _, candidate, _ = packet(db, run_id)
            return document_review.record(db, run_id, candidate, data.model_dump())

    @routes.post("/{run_id}/publish")
    def publish(run_id: str, request: Request) -> dict[str, Any]:
        with access(request) as db:
            return document_publication.publish_locked(config, db, run_id)

    return routes
