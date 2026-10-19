"""Local extractive answers. No model, network, publication or factual inference."""

import re
import sqlite3
import unicodedata
from typing import Any

from . import search
from .answer_contract import validate_selection
from .config import Config
from .inputs import text

# Same written unit only: no physical equivalence or subject/condition inference.
MEASURE = re.compile(r"(?<![\w.-])([+-]?\d+(?:\.\d+)?)\s*(psi|kPa|MPa|kW|°C|%)(?![A-Za-z])")


def comparisons(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    units: dict[str, list[dict[str, str]]] = {}
    for item in evidence:
        for match in MEASURE.finditer(unicodedata.normalize("NFKC", item["snippet"])):
            value, unit = match.groups()
            # Strip decimal zeroes without Decimal context rounding long source numbers.
            value = value.lstrip("+")
            if "." in value:
                value = value.rstrip("0").rstrip(".")
            negative = value.startswith("-")
            value = value.lstrip("-").lstrip("0") or "0"
            if value.startswith("."):
                value = "0" + value
            if negative and value != "0":
                value = "-" + value
            observation = {
                "citation": item["citation"],
                "value": value,
            }
            group = units.setdefault(unit, [])
            if observation not in group:
                group.append(observation)
    return [
        {
            "unit": unit,
            "observations": group,
            "status": "needs_context_review",
            "explanation": "不同数值可能对应不同对象、时间或条件，不能据此认定矛盾。",
        }
        for unit, group in sorted(units.items())
        if len({item["citation"] for item in group}) > 1
        and len({item["value"] for item in group}) > 1
    ]


def answer(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    question: str,
    retrieval_query: str,
    requested: list[str] | None = None,
    limit: int = 10,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Caller holds storage lock; evidence scope is resolved exclusively by the search layer."""
    text(question, limit=2000)
    retrieved = search.search(
        config, db, client_id, retrieval_query, requested, limit, session_id=session_id
    )
    evidence = retrieved["results"]
    proposal = {
        "mode": "extractive",
        "quotations": [
            {"citation": item["citation"], "quote": item["snippet"]} for item in evidence
        ],
    }
    quotations = validate_selection(proposal, evidence)
    differences = comparisons(evidence)
    cautions = [
        "仅呈现检索到的原文，未验证这些片段是否足以回答整个问题。",
        "未判断资料时效；没有日期不表示资料仍然有效。",
    ]
    if any(item["source_kind"] != "published_article" for item in evidence):
        cautions.append("存在未核实历史笔记、会话资料或用户修订，不视为独立核验结论。")
    if differences:
        cautions.append("检测到同单位不同数值，需结合来源上下文核对，不自动裁决。")
    response_text = (
        "在当前授权范围和检索词下未找到证据，无法回答。"
        if not evidence
        else "找到以下原文证据；尚不能据此给出确定结论。"
    )
    response = {
        "status": "insufficient_evidence" if not evidence else "evidence_only",
        "mode": "extractive",
        "question": question,
        "retrieval_query": retrieval_query,
        "answer": response_text,
        "quotations": quotations,
        "sources": evidence,
        "comparisons": differences,
        "cautions": cautions,
        "model_used": False,
        "scope": retrieved["scope"],
        "searched_vault_ids": retrieved["searched_vault_ids"],
        "searched_session_id": retrieved.get("searched_session_id"),
        "semantic_sufficiency": "not_assessed",
        "conflict_assessment": "not_assessed",
    }

    from .expert_answers import enrich

    return enrich(config, db, client_id, question, response)
