"""Text-question routing and bounded, authorized multi-query evidence retrieval."""

import re
from typing import Any

from ..search_matching import terms


def question(body: str, enabled: bool = False, *, standalone: bool = False) -> str | None:
    from .query import PREFIXES

    if not isinstance(body, str):
        return None
    value = body.strip()
    if value.startswith(("查询本库", "查询[")):
        return None
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
    if standalone:
        return value
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


def evidence(config: Any, db: Any, channel: Any, value: str, retrieval: list[str]) -> Any:
    from .retrieval_evidence import retrieve

    return retrieve(config, db, channel, value, retrieval)


def recheck_evidence(config: Any, db: Any, channel: Any, source: Any) -> Any:
    if "retrieval_recipe" not in source:
        from .legacy_evidence import evidence as legacy

        return legacy(config, db, channel, source["question"], source["retrieval_queries"])
    from .retrieval_evidence import replay

    return replay(config, db, channel, source)
