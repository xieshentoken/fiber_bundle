"""Text-question routing and bounded, authorized multi-query evidence retrieval."""

import re
from typing import Any

from .. import evidence_answers
from ..search_matching import terms


def question(body: str, enabled: bool = False) -> str | None:
    from .query import PREFIXES

    if not isinstance(body, str):
        return None
    value = body.strip()
    for prefix in PREFIXES:
        if value.startswith(prefix):
            from ..inputs import text

            return text(value[len(prefix) :].strip(), limit=2000)
    if not enabled or not value or len(value) > 2000:
        return None
    # Links, explicit storage/expert/control commands and forwarded transcripts
    # retain the material pipeline. Only standalone owner text reaches this rule.
    if re.search(r"https?://|www\.", value, re.I) or value.startswith(
        (
            "/",
            "abort",
            "批次 ",
            "术语 ",
            "文档 ",
            "保存",
            "入库",
            "归档",
            "提取",
            "转发",
            "聊天记录",
        )
    ):
        return None
    if re.search(
        r"[?？]\s*$|^(?:请|帮我|能否|可以|什么|为何|为什么|如何|怎么|哪些|是否|介绍|解释|概括|总结|"
        r"比较|对比|查找|查询|分析|根据|基于|列出|列举|给出|罗列)|"
        r"(?:是什么|有什么|有哪些|怎么样|吗|么|呢)[。！!]?\s*$",
        value,
    ):
        return value
    return None


def queries(value: Any) -> list[str]:
    if not isinstance(value, dict) or set(value) != {"queries"}:
        raise ValueError("query_plan_invalid")
    items = value["queries"]
    if not isinstance(items, list) or not 1 <= len(items) <= 3:
        raise ValueError("query_plan_invalid")
    for item in items:
        terms(item)
    return list(dict.fromkeys(items))


def _body_context(config: Any, db: Any, channel: Any, item: Any, retrieval: list[str]) -> Any:
    """Use source-exact prose instead of a matching YAML tag or image filename."""
    if item.get("expert_projection") or item.get("expert_module_id"):
        return item
    from .. import auth, note_index, vault_scope, vaults
    from ..config import IntegrityError
    from ..search_matching import fold

    vault = auth.grant(db, channel["value"]["client_id"], item["vault_id"], "query")
    root = vaults.checked_vault(config, vault)
    if not vault_scope.for_vault(root, vault).allows(item["note_path"]):
        raise IntegrityError("channel_source_changed")
    note = note_index.read_note(root, item["note_path"], note_index.Limits().note_bytes)
    if note.sha256 != item["sha256"]:
        raise IntegrityError("channel_source_changed")
    front = re.match(r"\ufeff?---\r?\n.*?\r?\n(?:---|\.\.\.)\s*\r?\n", note.text, re.S)
    first_line = note.text[: front.end()].count("\n") if front else 0
    words = list(dict.fromkeys(fold(w) for term in retrieval for w in term.split()))
    choices = []
    for line, passage in note_index.passages(note.text):
        if (
            line <= first_line
            or len(passage.strip()) < 20
            or passage.lstrip().startswith(("#", "!", "[[", "- [[", "```", "<!--"))
        ):
            continue
        score = sum(min(fold(passage).count(word), 3) for word in words)
        if score:
            choices.append((score, len(passage), -line, passage))
    if choices:
        _, _, negative_line, snippet = max(choices)
        return {**item, "line": -negative_line, "line_end": -negative_line, "snippet": snippet}
    return item


def evidence(config: Any, db: Any, channel: Any, value: str, retrieval: list[str]) -> Any:
    from . import query

    sources: list[dict[str, Any]] = []
    seen = set()
    used: list[str] = []
    packets = []

    def collect(expressions: list[str]) -> None:
        for term in expressions:
            if len(sources) == 5:
                break
            packet = query.evidence(config, db, channel, value, term)
            packets.append(packet)
            before = len(sources)
            for item in packet["sources"]:
                key = (item["vault_id"], item["note_path"])
                if key not in seen and len(sources) < 5:
                    seen.add(key)
                    # Freeze prose using the expression that contributed this
                    # source; duplicate/empty searches are omitted on recheck.
                    item = _body_context(config, db, channel, item, [term])
                    sources.append({**item, "citation": "[" + str(len(sources) + 1) + "]"})
            if len(sources) > before:
                used.append(term)

    collect(retrieval)
    # Literal terms within an expression are ANDed. Relax an empty synonym
    # group locally, without another model call. Recheck only contributing
    # expressions: omitted empty/duplicate searches cannot change this packet.
    if not sources:
        alternatives = list(dict.fromkeys(word for term in retrieval for word in term.split()))
        if alternatives != retrieval:
            retrieval = alternatives
            collect(retrieval)
    response = packets[0]
    response.update(
        sources=sources,
        quotations=[{"citation": s["citation"], "quote": s["snippet"]} for s in sources],
        comparisons=evidence_answers.comparisons(sources),
        retrieval_queries=used or retrieval,
        status="evidence_only" if sources else "insufficient_evidence",
    )
    response.pop("expert_blocks", None)
    if sources:
        response["answer"] = "找到以下原文证据；尚不能据此给出确定结论。"
    from ..expert_answers import enrich

    return enrich(config, db, channel["value"]["client_id"], value, response)
