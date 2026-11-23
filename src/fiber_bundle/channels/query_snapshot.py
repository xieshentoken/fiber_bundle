"""Choose the frozen content boundary independently from full-library search indexes."""

from typing import Any

from ..config import InvalidInput
from ..search_snapshot import SearchSnapshot
from . import query_scope


def create(config: Any, db: Any, channel: Any, frozen_scope: Any = None) -> SearchSnapshot:
    if query_scope.content_scope(channel) == "moc_members":
        from ..moc_query_snapshot import MountedQuerySnapshot

        if frozen_scope is not None and (
            not isinstance(frozen_scope, dict)
            or not isinstance(frozen_scope.get("vaults"), dict)
            or set(frozen_scope["vaults"])
            != {v["id"] for v in query_scope.checked(config, db, channel)}
        ):
            raise InvalidInput("channel_query_scope_changed")
        return MountedQuerySnapshot(
            config, db, channel["value"]["client_id"], frozen_scope=frozen_scope
        )
    if frozen_scope is not None:
        raise InvalidInput("channel_query_scope_changed")
    return SearchSnapshot(config, db, channel["value"]["client_id"])


def empty_response(config: Any, db: Any, channel: Any, question: str) -> Any:
    """No model admission when there are no mounted members in the selected libraries."""
    if query_scope.content_scope(channel) != "moc_members":
        return None
    from ..moc_query_snapshot import MountedQuerySnapshot

    selected = query_scope.checked(config, db, channel)
    with MountedQuerySnapshot(config, db, channel["value"]["client_id"]) as snapshot:
        if any(snapshot.has_members(vault) for vault in selected):
            return None
        coverage = {v["id"]: snapshot.coverage(v["id"]) for v in selected}
        incomplete = any(value and not value["complete"] for value in coverage.values())
        unknown = any(
            value and value.get("candidate_unavailable_count") for value in coverage.values()
        )
        unavailable = any(value and value["unavailable_count"] for value in coverage.values())
        result = {
            "status": "insufficient_evidence",
            "scope": "moc_members",
            "question": question,
            "sources": [],
            "answer": (
                "部分人工 MOC 关系候选暂不能核对，当前没有已确认可查询的成员，尚未调用模型。"
                if unknown and not unavailable
                else "所选知识库的已挂载 MOC 成员暂不可用，未取得可核对的资料，尚未调用模型。"
                if incomplete
                else "所选知识库当前没有可查询的已挂载 MOC 成员，尚未调用模型。"
            ),
            "model_used": False,
            "searched_vault_ids": [v["id"] for v in selected],
            "moc_query_scope": snapshot.scope_descriptor(),
            "coverage": coverage,
            "coverage_complete": not incomplete,
        }
        query_scope.annotate(config, db, channel, result)
        return result
