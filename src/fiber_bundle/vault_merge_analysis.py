"""Keep saved Agent analyses separate from source evidence through an explicit Vault merge."""

import json
from typing import Any

from . import auth


def records(db: Any, vault: str) -> list[dict[str, Any]]:
    result = []
    for row in db.execute(
        "SELECT run_id,intent_json FROM document_creation_saves "
        "WHERE vault_id=? AND state='published' ORDER BY run_id",
        (vault,),
    ):
        intent = json.loads(row["intent_json"])
        result.append(
            {
                "run_id": row["run_id"],
                "intent": intent,
                "note_path": intent["relative_path"] + "/note.md",
                "paths": [intent["relative_path"] + "/" + name for name in intent["files"]],
            }
        )
    for row in db.execute(
        "SELECT payload FROM vault_operations WHERE kind='vault_merge' AND"
        " state='complete' ORDER BY updated_at"
    ):
        value = json.loads(row[0])
        if value["plan"]["selection"]["target_vault_id"] != vault:
            continue
        for item in value["plan"]["files"]:
            if not item.get("analysis"):
                continue
            intent = dict(item["analysis"])
            intent["merged_source_vault_ids"] = sorted(
                set(intent.get("merged_source_vault_ids", []))
                | set(value["plan"]["selection"]["source_vault_ids"])
            )
            intent["files"] = {"note.md": {"sha256": item["projected_sha256"]}}
            result.append(
                {
                    "run_id": "merge:" + item["target_path"],
                    "intent": intent,
                    "note_path": item["target_path"],
                    "paths": [item["target_path"]],
                }
            )
    return result


def provenance(db: Any, client: str, vault: str, path: str, sha: str) -> tuple[str, str] | None:
    for row in db.execute(
        "SELECT payload FROM vault_operations WHERE kind='vault_merge' AND state='complete' "
        "ORDER BY updated_at DESC"
    ):
        value = json.loads(row[0])
        if value["plan"]["selection"]["target_vault_id"] != vault:
            continue
        for item in value["plan"]["files"]:
            analysis = item.get("analysis")
            if item["target_path"] != path or not analysis:
                continue
            # The frozen allowlist explicitly approves the target audience. Unmerged
            # source dependencies retain their own current query authorization.
            for source in analysis["source_vault_ids"]:
                if source not in set(value["plan"]["selection"]["source_vault_ids"]) | set(
                    analysis.get("merged_source_vault_ids", [])
                ):
                    auth.grant(db, client, source, "query")
                    auth.grant(db, analysis["client_id"], source, "query")
            kind = (
                "agent_analysis"
                if sha == item["projected_sha256"] and not analysis.get("user_revision")
                else "user_revision"
            )
            return kind, "已有 Agent 分析，非独立证据；关键事实须回查原始来源"
    return None
