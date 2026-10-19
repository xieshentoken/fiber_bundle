"""Authenticated automatic intake configuration and explicit batch controls."""

from typing import Any

from fastapi import APIRouter, Request, Response

from ..config import Config
from ..query_http import access
from . import (
    intake_actions,
    intake_contract,
    intake_originals,
    intake_receipts,
    intake_repository,
    intake_reviews,
)


def router(config: Config) -> APIRouter:
    routes = APIRouter()

    @routes.get("/{identity}/intake")
    def inspect(identity: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return intake_actions.inspect(config, identity)

    @routes.put("/{identity}/intake")
    def save(identity: str, data: intake_contract.Save, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return intake_repository.save(config, identity, data, leased=True)

    @routes.post("/{identity}/intake/batches", status_code=202)
    def selected(
        identity: str, data: intake_contract.BatchAction, request: Request
    ) -> dict[str, Any]:
        with access(config, request, admin=True):
            return intake_actions.select(config, identity, data, leased=True)

    @routes.post("/{identity}/intake/cancel")
    def cancel(
        identity: str, data: intake_contract.BatchAction, request: Request
    ) -> dict[str, Any]:
        with access(config, request, admin=True):
            return intake_actions.cancel(config, identity, data, leased=True)

    @routes.get("/{identity}/intake/batches/{batch}/originals")
    def originals(identity: str, batch: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return intake_originals.listing(config, identity, batch, leased=True)

    @routes.get("/{identity}/intake/batches/{batch}/originals/{asset_id}")
    def original(identity: str, batch: str, asset_id: str, request: Request) -> Response:
        from ..download_names import disposition

        with access(config, request, admin=True):
            data, mime, name = intake_originals.read(config, identity, batch, asset_id, leased=True)
            return Response(
                data, media_type=mime, headers={"Content-Disposition": disposition(name)}
            )

    @routes.post("/{identity}/intake/batches/{batch}/receipt-ack")
    def receipt_ack(
        identity: str, batch: str, data: intake_contract.Acknowledgement, request: Request
    ) -> dict[str, str]:
        with access(config, request, admin=True):
            return intake_receipts.reconcile(
                config, identity, batch, data.acknowledgement, leased=True
            )

    @routes.get("/{identity}/intake/batches/{batch}/links/{index}")
    def packet(identity: str, batch: str, index: int, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return intake_reviews.packet(config, identity, batch, index, leased=True)

    @routes.get("/{identity}/intake/batches/{batch}/links/{index}/assets/{name:path}")
    def asset(identity: str, batch: str, index: int, name: str, request: Request) -> Response:
        from ..download_names import disposition

        with access(config, request, admin=True):
            data, mime = intake_originals.derived(config, identity, batch, index, name, leased=True)
            return Response(
                data, media_type=mime, headers={"Content-Disposition": disposition(name)}
            )

    @routes.post("/{identity}/intake/batches/{batch}/links/{index}/review")
    def review(
        identity: str, batch: str, index: int, data: intake_contract.Review, request: Request
    ) -> dict[str, Any]:
        with access(config, request, admin=True):
            return intake_reviews.review(
                config, identity, batch, index, data.digest, data.reason, leased=True
            )

    @routes.post("/{identity}/intake/batches/{batch}/links/{index}/publish")
    def publish(
        identity: str, batch: str, index: int, data: intake_contract.Publish, request: Request
    ) -> dict[str, Any]:
        with access(config, request, admin=True):
            return intake_reviews.publish(
                config, identity, batch, index, data.review_id, leased=True
            )

    @routes.post("/{identity}/intake/batches/{batch}/links/{index}/retry", status_code=202)
    def retry(
        identity: str, batch: str, index: int, data: intake_contract.Retry, request: Request
    ) -> dict[str, Any]:
        from .intake_extraction import retry as retry_link

        with access(config, request, admin=True):
            return retry_link(
                config,
                identity,
                batch,
                index,
                data.expected_revision,
                leased=True,
                approve_new_model_attempt=data.approve_new_model_attempt,
                video_note_mode=data.video_note_mode,
            )

    return routes
