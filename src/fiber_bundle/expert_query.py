"""Query a selected, authorized text projection without copying binary package members."""

import sqlite3
from pathlib import Path
from typing import Any

from . import expert_media, expert_reference, published_expert
from .config import Config, InvalidInput, contained, make_directory
from .inputs import canonical
from .objects import digest
from .project_skill_contract import SEARCH_RUNTIME_SHA256
from .query_policy_store import write_private


def query(
    config: Config,
    db: sqlite3.Connection,
    cid: str,
    module_id: str,
    expected: str,
    question: str,
    limit: int = 5,
) -> dict[str, Any]:
    if (
        not module_id.startswith("expert:")
        or not isinstance(question, str)
        or not question.strip()
        or not 1 <= len(question) <= 2000
        or not 1 <= limit <= 8
    ):
        raise InvalidInput("expert_query_scope")
    identity = module_id[7:]
    module = published_expert.text(config, db, cid, identity, expected)
    result = execute(module, question, limit, allow_legacy=False)
    current = published_expert.text(config, db, cid, identity, expected)
    return deliver(module, current, result)


def execute(
    module: dict[str, Any], question: str, limit: int, *, allow_legacy: bool = True
) -> dict[str, Any]:
    catalog = module.get("catalog") or {}
    if catalog.get("schema_version") == "tkc.runtime-catalog/v0.3":
        if catalog.get("runtime_version") != "reference-search-bm25-v0.3":
            raise InvalidInput("project_skill_protocol_incompatible")
        if (
            not module.get("host_reference_adapter")
            and digest(module["files"].get("scripts/query_reference.py", b""))
            != SEARCH_RUNTIME_SHA256
        ):
            raise InvalidInput("knowledge_module_invalid")
        from .vendor.expert_reference_search import query_package

        try:
            return query_package(catalog, question, limit=limit)
        except (ValueError, TypeError, KeyError) as error:
            raise InvalidInput("knowledge_module_invalid") from error
    if module.get("host_reference_adapter") or not allow_legacy:
        raise InvalidInput("knowledge_legacy_text_only")
    import tempfile

    with tempfile.TemporaryDirectory(prefix="fiber-expert-text-") as temporary:
        folder = Path(temporary)
        for name, raw in module["files"].items():
            path = contained(folder, name)
            make_directory(path.parent)
            write_private(path, raw)
        return expert_reference.run_package(folder, module["runtime"]["image"], question, limit)


def deliver(
    module: dict[str, Any], current: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    if len(canonical(result).encode()) > 262144:
        raise InvalidInput("knowledge_evidence_capacity")
    if (
        current["package_sha256"] != module["package_sha256"]
        or current["vault_id"] != module["vault_id"]
        or current["version"] != module["version"]
    ):
        raise InvalidInput("knowledge_version_stale")
    packet = {**published_expert.public(module), "query_result": result, "model_called": False}
    return {**packet, "expert_blocks": [expert_media.block(packet)]}
