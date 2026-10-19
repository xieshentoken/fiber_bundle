"""Authenticated directory metadata and draft save settings; no publication execution."""

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request

from . import vault_directories, vault_save_policy
from .config import Config
from .query_http import access
from .vault_save_contract import Abandon, Discovery, Preview, Save


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/admin/vault-catalog")

    @routes.post("/directory-preview")
    def discover(data: Discovery, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return vault_directories.preview(
                config,
                db,
                root=data.root,
                scope=data.scope.model_dump(),
                preview_id=data.directory_preview_id,
                relative_parent=data.relative_parent,
                cursor=data.cursor,
            )

    @routes.get("/{vault_id}/directories")
    def directories(
        vault_id: str,
        request: Request,
        relative_parent: Annotated[str, Query(max_length=1024)] = "",
        cursor: Annotated[str | None, Query(max_length=100)] = None,
    ) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return vault_directories.listing(config, db, vault_id, relative_parent, cursor)

    @routes.get("/{vault_id}/save-policy")
    def current(vault_id: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return vault_save_policy.get(config, db, vault_id)

    @routes.post("/{vault_id}/save-policy/preview")
    def preview(vault_id: str, data: Preview, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return vault_save_policy.preview(
                config,
                db,
                vault_id,
                data.settings,
                data.expected_revision,
                enable_publication=data.enable_publication,
            )

    @routes.put("/{vault_id}/save-policy")
    def save(vault_id: str, data: Save, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return vault_save_policy.save(
                config, db, vault_id, data.preview_id, data.expected_revision
            )

    @routes.post("/{vault_id}/save-policy/abandon")
    def abandon(vault_id: str, data: Abandon, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return vault_save_policy.abandon(config, db, vault_id, data.operation_id)

    return routes
