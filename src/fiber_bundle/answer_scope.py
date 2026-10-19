"""Resolve and reauthorize frozen multi-Vault note scopes; never read note contents."""

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from . import search, vaults
from .answer_generation import AnswerMode
from .config import Config, IntegrityError, InvalidInput


@dataclass(frozen=True)
class ScopedNoteAnswerInput:
    vault_ids: tuple[str, ...] | None
    question: str
    retrieval_query: str
    limit: int = 10
    submission_id: str | None = None
    mode: AnswerMode = field(default="extractive", kw_only=True)


def resolve(
    config: Config, db: sqlite3.Connection, client_id: str, requested: tuple[str, ...] | None
) -> list[str]:
    selected = search.scope(db, client_id, None if requested is None else list(requested))
    if not selected:
        raise InvalidInput("answer scope is empty")
    for vault in selected:
        vaults.checked_vault(config, vault)
    return [v["id"] for v in selected]


def stored(run: sqlite3.Row, request: dict[str, Any]) -> list[str]:
    """Legacy single-Vault tasks retain their original scope without JSON changes."""
    if "scope_request" not in request:
        if "vault_ids" in request:
            raise IntegrityError("answer scope declaration missing")
        return [run["vault_id"]]
    ids = request.get("vault_ids")
    requested = request["scope_request"]
    if (
        not isinstance(ids, list)
        or not 1 <= len(ids) <= 20
        or any(not isinstance(v, str) or not v for v in ids)
        or len(set(ids)) != len(ids)
        or ids[0] != run["vault_id"]
        or (requested is not None and requested != ids)
        or run["binding_kind"] != "notes"
    ):
        raise IntegrityError("answer scope binding invalid")
    return ids


def evidence_snapshot(
    request: dict[str, Any], sources: list[dict[str, Any]]
) -> list[dict[str, Any]] | dict[str, Any]:
    if "scope_request" not in request:
        return sources
    # Older single-Vault workers compare a bare list: this envelope fails closed there.
    return {
        "schema": "scoped-note-evidence/v1",
        "vault_ids": request["vault_ids"],
        "sources": sources,
    }
