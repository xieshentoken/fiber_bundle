"""Human-facing channel prose; structured evidence and validation remain unchanged."""

import re
from typing import Any

SOURCE = r"(?:引用|出处|来源|参考文献|参考资料|原文|笔记(?:标题|路径))"
POSITIVE = re.compile(
    r"(?:注明|标注|标明|列出|提供|附上|附带|给出|给我|告诉我|告知|展示|显示|保留|返回|带上|加上|说明|查看|看看)"
    r"[^，,。；;！？!?\n]{0,12}"
    + SOURCE
    + "|"
    + SOURCE
    + r"(?:是什么|是哪里|有哪些|在哪里|呢|给我)"
    r"|\bcite\b|\b(?:with|include|show|list|provide|give|add)\s+(?:the\s+|your\s+)?"
    r"(?:sources?|citations?|references?|original\s+text)\b",
    re.I,
)
NEGATIVE = re.compile(
    r"(?:不要|无需|不必|不用|不需要|别|省略|去掉|不展示|不显示|不返回|不列|不附|不提供|不注明)"
    r"[^，,。；;！？!?\n]{0,12}"
    + SOURCE
    + r"|\b(?:without|no|omit)\s+(?:sources?|citations?|references?)\b"
    r"|\b(?:do\s+not|don't)\s+(?:include|show|list|provide|cite)\b",
    re.I,
)


def sources_requested(question: str) -> bool:
    requested = False
    for clause in re.split(r"[，,。；;！？!?\n]|但是|但|而是", question):
        if NEGATIVE.search(clause):
            requested = False
        elif POSITIVE.search(clause):
            requested = True
    return requested


def originals_requested(question: str) -> bool:
    requested = False
    for clause in re.split(r"[，,。；;！？!?\n]|但是|但|而是", question):
        if "原文" in clause:
            if NEGATIVE.search(clause):
                requested = False
            elif POSITIVE.search(clause):
                requested = True
    return requested


def caption(item: dict[str, Any]) -> str:
    value = str(item.get("caption") or "").strip()
    if not value or value == item.get("resource_id") or re.fullmatch(r"(?:vo|ko|ev)-[\w-]+", value):
        return "相关表格" if item.get("kind") == "table" else "相关图片"
    return value


def text(response: dict[str, Any]) -> str:
    question = str(response.get("question") or "")
    show_sources = sources_requested(question)
    claims = response.get("claims", [])
    parts = []
    if response.get("status") == "query_hint":
        return str(response["answer"])
    if response.get("status") == "query_failed":
        code = re.search(r"\b[a-z]+(?:_[a-z]+)+\b", str(response.get("answer") or ""))
        reasons = {
            "channel_source_changed": "资料在处理期间发生变化，已停止生成回答。",
            "channel_model_policy_changed": "模型配置在处理期间发生变化，已停止生成回答。",
            "channel_model_result_unknown": "模型调用结果未能确认，已停止处理以避免重复消耗。",
            "model_timeout": "模型响应超时，本次没有自动重试。",
            "model_key_budget_exhausted": "模型消耗已达到配置的预算，暂时无法完成回答。",
            "processing_key_budget_unconfigured": "当前模型尚未配置消耗预算，暂时无法完成回答。",
            "model_unconfigured": "尚未配置用于回答的模型。",
            "model_disabled": "用于回答的模型已停用。",
            "note_index_update_required": "知识库索引需要更新，暂时无法完成回答。",
            "answer_selection_quote_mismatch": "模型返回的引用与资料原文不一致，已停止发送回答。",
        }
        explanation = reasons.get(code[0] if code else "", "本次问答未能完成。")
        return explanation + "请在消息入口核对详情后再提问。"
    if claims:
        for claim in claims:
            value = claim["text"]
            if show_sources:
                value += " " + " ".join(claim["citations"])
            parts.append(value)
    elif response.get("mode") == "cited_draft":
        parts.append("现有资料不足以可靠回答这个问题。")
    elif response.get("sources"):
        # Evidence-only mode still shows useful prose, without a locator dump.
        for source in response["sources"]:
            value = source["snippet"]
            if show_sources:
                value = source["citation"] + " " + value
            if value not in parts:
                parts.append(value)
    else:
        parts.append(str(response.get("answer") or ""))
    blocks = response.get("expert_blocks", [])
    for block in blocks:
        if not claims and block.get("text") and block["text"] not in parts:
            parts.append(block["text"])
        if block.get("behavior") == "abstain":
            parts.append("专家资料中的文字不足以支持结论，相关图表仅供参考。")
    if claims or blocks:
        parts.append("以上内容依据现有资料整理，尚未独立核验。")
    if show_sources and response.get("sources"):
        sources = []
        for source in response["sources"]:
            label = str(source.get("title") or "相关资料")
            if source.get("vault_alias"):
                label = "[" + source["vault_alias"] + "] " + label
            location = source.get("source_url") or source.get("note_path")
            if location:
                label += " · " + location
            sources.append(source["citation"] + " " + label)
        parts.append("来源：\n" + "\n".join(sources))
    if claims and originals_requested(question):
        for source in response.get("sources", []):
            parts.append("原文片段：" + source["snippet"])
    if response.get("query_scope_labels"):
        parts.insert(0, "查询范围：" + "、".join(response["query_scope_labels"]))
    return "\n\n".join(dict.fromkeys(p for p in parts if p))


def missing_media(response: dict[str, Any]) -> str:
    from ..expert_presentation import reason

    return "\n\n".join(
        caption(item) + "：" + reason(item)
        for block in response.get("expert_blocks", [])
        for item in block.get("media", [])
        if item.get("status") != "available"
    )
