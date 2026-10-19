"""Administrator-only merge transport; concrete preview/freeze precedes any execution."""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import Field

from . import (
    vault_merge_execution,
    vault_merge_inventory,
    vault_merge_plan,
    vault_merge_recovery,
)
from .config import Config
from .payloads import Identifier, Payload
from .query_http import access


class Freeze(Payload):
    selection: vault_merge_plan.Selection
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Confirmation(Payload):
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Start(Confirmation):
    backup_path: str = Field(min_length=1, max_length=4096)
    backup_password: str = Field(min_length=1, max_length=4096, repr=False)


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/admin/vault-merges")

    @routes.get("/inventory/{vault_id}")
    def inventory(vault_id: str, client_id: Identifier, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            value = vault_merge_inventory.describe(config, db, client_id, vault_id)
            managed = {p for pub in value["publications"] for p in pub["mapping"].values()}
            return {
                "vault_id": vault_id,
                "files": [
                    {"path": f["path"], "bytes": f["bytes"], "managed": f["path"] in managed}
                    for f in value["files"]
                ],
            }

    @routes.post("/preview")
    def preview(data: vault_merge_plan.Selection, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            value = vault_merge_plan.preflight(config, db, data)
            return public_preview(value)

    @routes.post("/freeze")
    def freeze(data: Freeze, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return vault_merge_plan.freeze(config, db, data.selection, data.plan_sha256)

    @routes.get("")
    def listing(request: Request) -> list[dict[str, Any]]:
        from .vault_merge_journal import view

        with access(config, request, admin=True) as (db, _):
            return [
                view(db, r[0])
                for r in db.execute(
                    "SELECT id FROM vault_operations WHERE kind='vault_merge' ORDER BY "
                    "created_at DESC LIMIT 100"
                )
            ]

    @routes.get("/{identity}")
    def status(identity: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return vault_merge_execution.view(config, identity)

    @routes.post("/{identity}/start")
    def start(identity: str, data: Start, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return vault_merge_execution.start(
                config, identity, data.plan_sha256, Path(data.backup_path), data.backup_password
            )

    @routes.post("/{identity}/cancel")
    def cancel(identity: str, data: Confirmation, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return vault_merge_recovery.cancel(config, identity, data.plan_sha256)

    @routes.post("/{identity}/resume")
    def resume(identity: str, data: Confirmation, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return vault_merge_recovery.resume(config, identity, data.plan_sha256)

    @routes.post("/{identity}/rollback")
    def rollback(identity: str, data: Confirmation, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return vault_merge_recovery.rollback(config, identity, data.plan_sha256)

    return routes


def public_preview(value: dict[str, Any]) -> dict[str, Any]:
    return {
        k: value[k]
        for k in (
            "schema",
            "plan_sha256",
            "ready",
            "copy_bytes",
            "cross_filesystem",
            "conflicts",
            "excluded",
            "source_files_retained",
        )
    } | {
        "files": [
            {
                k: f[k]
                for k in (
                    "source_vault_id",
                    "source_path",
                    "target_path",
                    "role",
                    "projected_sha256",
                    "projected_bytes",
                )
            }
            for f in value["files"]
        ],
        "sources": [
            {
                "vault_id": s["context"]["vault_id"],
                "notes": len(s["notes"]),
                "original_versions": len(s["sources"]),
                "mocs": len(s["mocs"]),
            }
            for s in value["inventories"][:-1]
        ],
        "target_grants": value["selection"]["target_grants"],
        "paused_channels": [c["id"] for c in value["entrances"]["channels"]],
        "switch_channel_ids": value["selection"]["switch_channel_ids"],
    }
