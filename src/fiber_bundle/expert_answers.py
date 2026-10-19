"""Enrich only registered expert hits; ordinary notes remain text-only evidence."""

import sqlite3
from typing import Any

from . import auth, expert_media, expert_query
from .config import Config, IntegrityError, InvalidInput


def enrich(
    config: Config, db: sqlite3.Connection, cid: str, question: str, response: dict[str, Any]
) -> dict[str, Any]:
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for source in response["sources"]:
        if source.get("expert_projection") is True:
            key = (source["expert_module_id"], source["expert_module_version"])
            selected.setdefault(key, source)
    if not selected:
        return response
    blocks = []
    for (module_id, version), source in selected.items():
        block = {
            "module_id": module_id,
            "version": version,
            "source_version_id": source.get("source_version_id"),
            "artifact_stage": "draft",
            "review_state": "deferred",
            "knowledge_verified": False,
            "independent_evidence": False,
            "citation": source["citation"],
            "evidence_ids": [],
            "object_ids": [],
            "text": source["snippet"],
            "media": [],
        }
        try:
            packet = expert_query.query(config, db, cid, module_id, version, question, 5)
            block.update(expert_media.block(packet))
        except (auth.Denied, InvalidInput, IntegrityError, OSError, ValueError) as error:
            block.update(
                behavior="abstain",
                reason_codes=["expert_text_unavailable"],
                missing_reason=(
                    "旧版专家包保持原有文字检索，未扩展图表。"
                    if str(error) == "knowledge_legacy_text_only"
                    else "专家文字暂不可用，保留已检索文字。"
                ),
            )
        blocks.append(block)
    return {**response, "expert_blocks": blocks}
