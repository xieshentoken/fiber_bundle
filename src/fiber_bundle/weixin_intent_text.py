"""Conservative source/question boundaries from owner messages; no model or side effects."""

import re
from typing import Any

from .config import InvalidInput
from .weixin_note_queries import PREFIXES
from .weixin_note_queries import question as note_question

QUESTION_LINE = re.compile(r"(?m)^(?:问题|咨询|提问)[：:]\s*")
REQUEST = re.compile(
    r"^(?:请(?:帮我)?|帮我|麻烦(?:你)?|能否|可以(?:帮我)?|我想)(?:总结|解释|分析|比较|对比|介绍|了解|知道|回答|看看)"
)
INTERROGATIVE = re.compile(r"什么|哪些|为什么|怎么|如何|是否|多少|有没有|能不能|区别|谁|哪|吗|么")
CONSULT = frozenset({"仅用于咨询", "只咨询", "仅咨询", "帮我看看", "请帮我看看"})
SAVE = re.compile(
    r"^(?:也保存|同时保存|顺便保存|保存(?:刚才|刚刚|上面|这|那).*(?:份|篇|资料|原文)|请保存)$"
)
EXTRA_SAVE = re.compile(
    r"(?:^|[，,；;。?？\n])\s*(?:请)?(?:也保存|同时保存|顺便保存|也存下来|也入库)"
    r"(?:原资料|原文|这份资料|资料|它)?[。.!！]*$"
)
SAVE_CLAUSE = re.compile(
    r"(?:^|[，,；;。?？\n])\s*(?:请)?(?:也保存|同时保存|顺便保存|也存下来|也入库)"
)
NO_SAVE = re.compile(r"不要保存|不用保存|不入库|只(?:做)?咨询|仅用于咨询|仅咨询")
UNSUPPORTED = re.compile(r"^(?:删除|清空|新话题|清理会话|停止任务|取消)")
UNKNOWN_REQUEST = re.compile(r"^(?:请|帮我|麻烦|我想|我需要|能否|可以帮我|保存|归档|入库|不要|别)")


def _question(value: str) -> bool:
    value = value.strip()
    return bool(
        REQUEST.match(value)
        or (
            "\n" not in value
            and INTERROGATIVE.search(value)
            and (
                value.endswith(("?", "？", "吗", "么"))
                or value.startswith(("为什么", "如何", "怎么"))
            )
        )
    )


def _text(
    value: str, *, note_queries: bool = False
) -> tuple[list[int] | None, str | None, bool, str | None]:
    """Ranges index original Unicode text, which stays intact in the journal."""
    stripped = value.strip()
    if note_queries and stripped.startswith(PREFIXES):
        try:
            note_question(stripped)
        except InvalidInput:
            return None, None, False, "question_required"
        return None, stripped, False, None
    if stripped in CONSULT:
        return None, None, False, "question_required"
    if "\n" not in stripped and NO_SAVE.match(stripped):
        return None, None, False, "question_required"
    trailing_save = False
    trailing_consult = False
    if "\n" in value:
        prefix, _, final = value.rpartition("\n")
        if SAVE.fullmatch(final.strip()):
            value, stripped, trailing_save = prefix, prefix.strip(), True
        elif final.strip() in CONSULT or NO_SAVE.fullmatch(final.strip()):
            value, stripped, trailing_consult = prefix, prefix.strip(), True
    marker = QUESTION_LINE.search(value)
    if marker is None and stripped.startswith(("资料：", "资料:", "原文：", "原文:")):
        return (
            [0, len(value)],
            None,
            trailing_save,
            "question_required" if trailing_consult else None,
        )
    body: list[int] | None = [0, len(value)]
    question = None
    if marker:
        body = [0, marker.start()] if value[: marker.start()].strip() else None
        question = value[marker.end() :].strip() or None
    elif _question(stripped):
        body, question = None, stripped
    elif "\n" in value:
        prefix, _, final = value.rpartition("\n")
        if _question(final):
            body, question = [0, len(prefix)], final.strip()
    if question is not None:
        if len(question) > 2000:
            return body, None, False, "question_too_long"
        if (trailing_save or SAVE_CLAUSE.search(question)) and (
            trailing_consult or NO_SAVE.search(question)
        ):
            return body, None, False, "conflicting_save_request"
        return body, question, trailing_save or bool(EXTRA_SAVE.search(question)), None
    if trailing_consult:
        return body, None, False, "question_required"
    if stripped in CONSULT or (marker and question is None):
        return body if marker else None, None, False, "question_required"
    if SAVE.fullmatch(stripped):
        return None, None, True, None
    if UNSUPPORTED.match(stripped):
        return None, None, False, "unsupported_instruction"
    if UNKNOWN_REQUEST.match(stripped):
        return None, None, False, "unrecognized_request"
    if NO_SAVE.search(stripped):
        return body, None, False, "unrecognized_request"
    return body, None, trailing_save, None


def retrieval_query(question: str) -> str:
    """Strip recognized request scaffolding only; no semantic expansion or broader scope."""
    value = EXTRA_SAVE.sub("", question).strip(" ，,；;。?？\n")
    match = REQUEST.match(value)
    if match:
        value = value[match.end() :].strip(" ：:，,。?？\n")
    return value or question


def classify(message: dict[str, Any], *, version: int = 2) -> dict[str, Any]:
    """Forwarded/referenced source instructions are data. Unclear requests await clarification."""
    if type(version) is not int or version not in (1, 2):
        raise InvalidInput("unsupported intent version")
    source_keys: dict[str, list[int] | None] = {}
    questions = []
    also_save = False
    reasons = []
    forwarded = any(message.get(k) for k in ("ref_msg", "forward_info", "forwarded"))

    def item(value: dict[str, Any], key: str, *, reference: bool = False) -> None:
        nonlocal also_save
        kind = value.get("type")
        if type(kind) is not int:
            reasons.append("unsupported_source")
            return
        if kind == 1:
            content = value.get("text_item")
            if not isinstance(content, dict) or not isinstance(content.get("text"), str):
                reasons.append("unsupported_source")
                return
            text = content["text"]
            if forwarded or reference or value.get("forwarded") or value.get("forward_info"):
                source_keys[key] = [0, len(text)]
            else:
                span, question, save, reason = _text(text, note_queries=version == 2)
                if span is not None and text[span[0] : span[1]].strip():
                    source_keys[key] = span
                if question:
                    questions.append(question)
                also_save |= save
                if reason:
                    reasons.append(reason)
        elif kind in (2, 3, 4, 5):
            source_keys[key] = None
        else:
            reasons.append("unsupported_source")
        if isinstance(value.get("ref_msg"), dict):
            if reference:
                reasons.append("unsupported_source")
                return  # The existing event contract supports only one reference level.
            nested = value["ref_msg"].get("message_item")
            if isinstance(nested, dict):
                item(nested, key + ".ref", reference=True)

    for index, value in enumerate(message.get("item_list", [])):
        if isinstance(value, dict):
            item(value, str(index))
    if len(questions) > 1:
        reasons.append("multiple_questions")
    if any(NO_SAVE.search(q) for q in questions) and also_save:
        reasons.append("conflicting_save_request")
    return {
        "version": version,
        "source_keys": source_keys,
        "question": questions[0] if len(questions) == 1 else None,
        "also_save": also_save,
        "reason": reasons[0] if reasons else None,
        "consult": bool(questions) or "question_required" in reasons,
    }
