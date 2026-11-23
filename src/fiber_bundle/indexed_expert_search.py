"""Literal retrieval from admitted native or externally registered Draft versions."""

import sqlite3
from pathlib import PurePosixPath
from typing import Any

from . import indexed_sources, published_expert
from .config import Config, InvalidInput
from .search_matching import best_passage, terms


def search(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    identity: str,
    query: str,
    expected: str | None = None,
) -> list[Any]:
    module = published_expert.text(config, db, client, identity, expected)
    if not indexed_sources.expert_allowed(config, db, client, module):
        raise InvalidInput("knowledge_module_not_indexed")
    title = module.get("title") or PurePosixPath(module["entry_note_path"]).parent.name
    text = "\n".join(str(o.get("statement") or o.get("title") or "") for o in module["objects"])
    best = best_passage(title, text, terms(query))
    if best is None:
        return []
    return [
        {
            "vault_id": module["vault_id"],
            "note_path": module["entry_note_path"],
            "title": title,
            "sha256": module["version"],
            "line": best[1],
            "snippet": best[2],
            "line_end": best[1] + len(best[2].splitlines()) - 1,
            "source_kind": "agent_analysis",
            "source_label": "外部专家 Skill Draft（未经独立核验）"
            if module.get("host_reference_adapter")
            else "专家资料 Draft（未经独立核验）",
            "source_version_id": module.get("source_version_id"),
            "known_gaps": module.get("known_gaps", []),
            "save_basis": module.get("save_basis"),
            "expert_projection": True,
            "expert_module_id": module["module_id"],
            "expert_module_version": module["version"],
            "artifact_stage": "draft",
            "review_state": "deferred",
            "knowledge_verified": False,
            "independent_evidence": False,
            "completeness_status": module.get("completeness_status", "unknown"),
        }
    ]


def verify(config: Config, db: sqlite3.Connection, client: str, source: Any) -> dict[str, Any]:
    """Recheck canonical passages without another search or model invocation."""
    module = published_expert.text(
        config, db, client, source["expert_module_id"][7:], source["expert_module_version"]
    )
    if not indexed_sources.expert_allowed(config, db, client, module):
        raise InvalidInput("knowledge_module_not_indexed")
    text = "\n".join(str(o.get("statement") or o.get("title") or "") for o in module["objects"])
    start, end = source.get("line"), source.get("line_end")
    if (
        type(start) is not int
        or type(end) is not int
        or not 1 <= start <= end <= len(text.splitlines())
    ):
        raise InvalidInput("agent_evidence_invalid")
    return {
        **source,
        "vault_id": module["vault_id"],
        "note_path": module["entry_note_path"],
        "title": module.get("title") or PurePosixPath(module["entry_note_path"]).parent.name,
        "sha256": module["version"],
        "snippet": "\n".join(text.splitlines()[start - 1 : end]),
        "source_version_id": module.get("source_version_id"),
        "source_label": "外部专家 Skill Draft（未经独立核验）"
        if module.get("host_reference_adapter")
        else "专家资料 Draft（未经独立核验）",
        "known_gaps": module.get("known_gaps", []),
        "save_basis": module.get("save_basis"),
        "completeness_status": module.get("completeness_status", "unknown"),
    }
