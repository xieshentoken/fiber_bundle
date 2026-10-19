"""Thin authenticated GUI upload endpoints; no extraction or filesystem logic here."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request
from pydantic import Field

from . import source_import_execution, source_imports
from .config import Config
from .payloads import Identifier, Payload
from .query_http import Digest, access


class ImportRequest(Payload):
    client_id: Identifier
    vault_id: Identifier
    submission_id: Identifier
    filename: Annotated[str, Field(min_length=1, max_length=512)]
    source_sha256: Digest
    source_bytes: Annotated[int, Field(strict=True, ge=1, le=16 * 1024 * 1024)]
    configuration_sha256: Digest | None
    action: Literal["archive", "process"]
    engine: Literal["ordinary", "fake-expert"] = "ordinary"
    profile: Literal["native", "ocr", "conversion"] = "native"
    expert_profile: Literal["native-text", "scan-text", "scan-structure", "visual"] = "native-text"
    expert_backend: (
        Literal[
            "pypdf-native",
            "paddleocr-ppocrv6",
            "pymupdf-tesseract",
            "docling",
            "paddleocr-ppstructure-v3",
            "pdftoppm",
        ]
        | None
    ) = None
    workpack_import_id: Identifier | None = None
    references: dict[str, Identifier] = Field(default_factory=dict, max_length=32)
    overrides: dict[str, Annotated[int, Field(strict=True, ge=1)]] = Field(default_factory=dict)


class ImportChunk(Payload):
    client_id: Identifier
    ordinal: Annotated[int, Field(strict=True, ge=0, le=63)]
    sha256: Digest
    data: Annotated[str, Field(min_length=1, max_length=349528)]


class ImportOwner(Payload):
    client_id: Identifier


class ExpertPublish(ImportOwner):
    artifact_sha256: Digest
    source_reviewed: Annotated[bool, Field(strict=True)]
    candidate_acknowledged: Annotated[bool, Field(strict=True)]


class ExpertResume(ImportOwner):
    workpack_import_id: Identifier | None = None


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/admin/imports")

    @routes.get("")
    def listing(client_id: str, vault_id: str, request: Request) -> list[dict[str, Any]]:
        with access(config, request, admin=True) as (db, _):
            return source_imports.listing(config, db, client_id, vault_id)

    @routes.get("/expert-commands")
    def expert_commands(
        client_id: Identifier, vault_id: Identifier, request: Request
    ) -> list[dict[str, Any]]:
        from .expert_feishu import listing

        with access(config, request, admin=True) as (db, _):
            return listing(config, db, client_id, vault_id)

    @routes.post("", status_code=201)
    def create(data: ImportRequest, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return source_imports.create(config, db, data.model_dump())

    @routes.put("/{import_id}/chunks")
    def chunk(import_id: Identifier, data: ImportChunk, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return source_imports.chunk(
                config, db, data.client_id, import_id, data.ordinal, data.sha256, data.data
            )

    @routes.post("/{import_id}/finish")
    def finish(import_id: Identifier, data: ImportOwner, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return source_imports.finish(config, db, data.client_id, import_id)

    @routes.post("/{import_id}/start", status_code=202)
    def start(import_id: Identifier, data: ImportOwner, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return source_import_execution.launch(config, db, data.client_id, import_id)

    @routes.post("/{import_id}/cancel")
    def cancel(import_id: Identifier, data: ImportOwner, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return source_imports.cancel(config, db, data.client_id, import_id)

    @routes.post("/{import_id}/resume-expert", status_code=202)
    def resume_expert(
        import_id: Identifier, data: ExpertResume, request: Request
    ) -> dict[str, Any]:
        from .expert_dispatch import resume

        with access(config, request, admin=True) as (db, _):
            return resume(config, db, data.client_id, import_id, data.workpack_import_id)

    @routes.get("/{import_id}/expert-query")
    def query_expert(
        import_id: Identifier, client_id: Identifier, question: str, request: Request
    ) -> dict[str, Any]:
        from . import database
        from .expert_reference import query

        with database.locked(config), access(config, request, admin=True):
            pass
        result = query(config, client_id, import_id, question)
        with database.locked(config), access(config, request, admin=True):
            return result

    @routes.get("/{import_id}/source")
    def original(import_id: Identifier, client_id: Identifier, request: Request) -> Any:
        import json

        from starlette.responses import StreamingResponse

        from . import auth, database, document_bindings
        from .download_names import disposition, filename
        from .source_import_delivery import chunks

        with database.locked(config), access(config, request, admin=True) as (db, _):
            source = source_imports.get(config, db, client_id, import_id)
            bound = document_bindings.source(
                config, db, client_id, source["receipt_id"], source["asset_id"]
            )
        name = filename(json.loads(source["request_json"])["filename"], "application/octet-stream")
        return StreamingResponse(
            chunks(config, import_id, client_id, bound.file, request.cookies.get(auth.COOKIE, "")),
            media_type="application/octet-stream",
            headers={
                "Content-Length": str(bound.byte_count),
                "Content-Disposition": disposition(name),
            },
        )

    @routes.get("/{import_id}/expert")
    def expert(import_id: Identifier, client_id: Identifier, request: Request) -> dict[str, Any]:
        from .expert_evidence import describe

        with access(config, request, admin=True) as (db, _):
            return describe(config, db, client_id, import_id)

    @routes.get("/{import_id}/expert-package")
    def package(import_id: Identifier, client_id: Identifier, request: Request) -> Any:
        from starlette.responses import Response

        from .expert_evidence import bundle

        with access(config, request, admin=True) as (db, _):
            raw = bundle(config, db, client_id, import_id)
        return Response(
            raw,
            media_type="application/zip",
            headers={
                "Content-Disposition": "attachment; filename=reference-draft.zip",
            },
        )

    @routes.post("/{import_id}/publish-expert")
    def publish(import_id: Identifier, data: ExpertPublish, request: Request) -> dict[str, Any]:
        from .expert_publication import publish

        with access(config, request, admin=True) as (db, _):
            return publish(
                config,
                db,
                data.client_id,
                import_id,
                data.artifact_sha256,
                data.source_reviewed,
                data.candidate_acknowledged,
            )

    return routes
