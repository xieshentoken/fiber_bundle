"""Connection scope and quality are enforced by Fiber, never by Skill prose."""

import sqlite3
from typing import Any

from .. import auth
from ..config import Config
from . import store


def scope(
    config: Config, db: sqlite3.Connection, cid: str, requested: list[str] | None
) -> tuple[list[str] | None, str]:
    row = db.execute("SELECT id FROM agent_connections WHERE client_id=?", (cid,)).fetchone()
    if row is None:
        return requested, "all"
    context = store.context(config, db, row[0])
    if not context["enabled"]:
        raise auth.Denied(403)
    allowed = context["configuration"]["vault_ids"]
    if requested is not None and not set(requested) <= set(allowed):
        raise auth.Denied(404)
    return requested if requested is not None else allowed, context["configuration"].get(
        "quality_scope", "all"
    )


def visible(provenance: dict[str, Any], quality: str) -> bool:
    return quality == "all" or provenance.get("completeness_status") == "passed"
