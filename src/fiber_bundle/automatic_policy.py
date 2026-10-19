"""Explicit per-Vault automatic admission; reads never activate or enqueue work."""

import json
import sqlite3
import time
from typing import Any, Literal

from pydantic import Field

from . import auth, vault_save_policy, vaults
from .attachment_projection import epoch
from .config import Config, InvalidInput
from .execution_config import ExecutionConfig
from .inputs import canonical
from .payloads import Identifier, Payload


class Policy(Payload):
    policy_version: Literal[1, 2] = 1
    enabled: bool = False
    completeness_check: bool = True
    direct_to_inbox: bool = False
    processing_mode: Literal["basic", "pi_skill"] = "basic"
    extra_summary: bool = False
    model_configuration_id: str | None = None
    client_id: Identifier | None = None
    review_mode: Literal["manual", "automatic"] = "manual"
    review_method: Literal["rules", "model"] = "rules"
    publish_mode: Literal["manual", "automatic"] = "manual"
    model_config_path: str | None = Field(default=None, max_length=2000)
    allow_model_source_content: bool = False
    allowed_kinds: list[Literal["document", "article", "media", "intake", "expert"]] = Field(
        default_factory=list
    )


def get(db: sqlite3.Connection, vault: str) -> dict[str, Any]:
    if db.execute("SELECT 1 FROM vaults WHERE id=?", (vault,)).fetchone() is None:
        raise InvalidInput("automatic_vault_unavailable")
    row = db.execute("SELECT * FROM automatic_policies WHERE vault_id=?", (vault,)).fetchone()
    return {
        "revision": row["revision"] if row else 0,
        "settings": json.loads(row["value"]) if row else Policy().model_dump(),
        "updated_at": row["updated_at"] if row else None,
        "capabilities": {"rules": True, "model": True},
    }


def save(
    config: Config, db: sqlite3.Connection, vault: str, expected: int, settings: Policy
) -> dict[str, Any]:
    current = get(db, vault)
    if current["revision"] != expected:
        raise InvalidInput("automatic_policy_revision_changed")
    if len(settings.allowed_kinds) != len(set(settings.allowed_kinds)):
        raise InvalidInput("automatic_policy_duplicate_kind")
    if active(settings.model_dump()):
        if settings.client_id is None or not settings.allowed_kinds:
            raise InvalidInput("automatic_policy_scope_required")
        if settings.policy_version == 1 and settings.review_method == "model":
            from .automatic_model import route

            route(settings.model_dump())
        if settings.processing_mode == "pi_skill" or settings.extra_summary:
            processing_route(config, settings.model_dump())
        for permission in ("query", "original", "add"):
            vaults.checked_vault(config, auth.grant(db, settings.client_id, vault, permission))
        layout = vault_save_policy.get(config, db, vault)
        if not layout["publication_ready"] or layout["settings"].get("policy_version") != 2:
            raise InvalidInput("automatic_policy_requires_unified_layout")
    elif settings.policy_version == 1 and settings.publish_mode == "automatic":
        raise InvalidInput("automatic_publish_requires_automatic_review")
    db.execute(
        "INSERT INTO automatic_policies VALUES(?,?,?,?) ON CONFLICT(vault_id) DO UPDATE SET "
        "revision=excluded.revision,value=excluded.value,updated_at=excluded.updated_at",
        (vault, expected + 1, canonical(settings.model_dump()), time.time()),
    )
    return get(db, vault)


def binding(
    config: Config, db: sqlite3.Connection, vault: str, client: str, kind: str
) -> dict[str, Any] | None:
    policy = get(db, vault)
    settings = policy["settings"]
    if (
        not active(settings)
        or settings["client_id"] != client
        or kind not in settings["allowed_kinds"]
    ):
        return None
    for permission in ("query", "original", "add"):
        vaults.checked_vault(config, auth.grant(db, client, vault, permission))
    layout = vault_save_policy.get(config, db, vault)
    if not layout["publication_ready"] or layout["settings"].get("policy_version") != 2:
        raise InvalidInput("automatic_policy_layout_changed")
    from .automatic_model import route

    return {
        "policy_revision": policy["revision"],
        "settings": settings,
        "permission_epoch": epoch(db, client, vault),
        "layout_revision": layout["revision"],
        **(
            {"model_config_sha256": route(settings).digest}
            if settings.get("policy_version", 1) == 1 and settings["review_method"] == "model"
            else {}
        ),
        **(
            {"processing_model_sha256": processing_route(config, settings).digest}
            if kind != "intake"
            and (settings.get("processing_mode") == "pi_skill" or settings.get("extra_summary"))
            else {}
        ),
    }


def active(settings: dict[str, Any]) -> bool:
    return (
        bool(settings.get("enabled"))
        if settings.get("policy_version", 1) == 2
        else (settings["review_mode"] == "automatic")
    )


def direct(settings: dict[str, Any]) -> bool:
    return (
        bool(settings.get("direct_to_inbox"))
        if settings.get("policy_version", 1) == 2
        else (settings["publish_mode"] == "automatic")
    )


def processing_route(config: Config, settings: dict[str, Any]) -> ExecutionConfig:
    from .model_configuration.policy import resolve
    from .query_policy_store import legacy_current

    if not settings.get("allow_model_source_content"):
        raise InvalidInput("processing_cloud_content_not_allowed")
    reference = settings.get("model_configuration_id")
    if reference == "app-default" or (reference and reference.startswith("model_")):
        return resolve(config, reference)
    route = legacy_current(config)
    if reference != "opencodego-primary" or not route or not route.cloud_answer:
        raise InvalidInput("processing_model_unconfigured")
    route.cloud_answer.validate()
    return route.cloud_answer
