import json
from copy import deepcopy

import pytest

from fiber_bundle.answer_contract import SelectionRejected
from fiber_bundle.answer_drafts import DraftRejected
from fiber_bundle.answer_model_output import AnswerOutputRejected, parse_response
from fiber_bundle.channels import model_output

SOURCES = [
    {"citation": "[1]", "snippet": "第一段真实正文。"},
    {"citation": "[2]", "snippet": "第二段真实正文。"},
]


def reply(proposal, fenced=False):
    content = json.dumps(proposal, ensure_ascii=False)
    if fenced:
        content = "```json\n" + content + "\n```"
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": content,
                },
            }
        ]
    }


def test_host_supplies_frozen_quotes_without_model_echo():
    claims = [{"text": "概括正文。", "citations": ["[1]"]}]
    for extra in [
        {},
        {"mode": "cited_draft"},
        {"quotations": [{"citation": "[1]", "quote": "第一段真实正文。"}]},
    ]:
        value = model_output.accept(reply({"claims": claims, **extra}), SOURCES)
        assert value["claims"] == claims
        assert value["quotations"] == [
            {"citation": s["citation"], "quote": s["snippet"]} for s in SOURCES
        ]


def test_exact_json_fence_is_adapted_without_weakening_shared_parser():
    body = reply({"claims": []}, fenced=True)
    assert model_output.accept(body, SOURCES)["claims"] == []
    with pytest.raises(AnswerOutputRejected):
        parse_response(body)
    body["choices"][0]["message"]["content"] += "额外说明"
    with pytest.raises(AnswerOutputRejected):
        model_output.parse(body)


@pytest.mark.parametrize(
    "proposal",
    [
        {"claims": [{"text": "虚构引用。", "citations": ["[99]"]}]},
        {"claims": [{"text": "把[1]写在正文。", "citations": ["[1]"]}]},
        {"claims": [], "mode": "extractive"},
        {"claims": [], "answer": "额外字段"},
        {"claims": [], "quotations": [{"citation": "[1]", "quote": "篡改的引文"}]},
        {"claims": [], "quotations": [{"citation": "[99]", "quote": "伪造来源"}]},
        {
            "claims": [],
            "quotations": [
                {"citation": "[1]", "quote": "第一段真实正文。"},
                {"citation": "[1]", "quote": "第一段真实正文。"},
            ],
        },
    ],
)
def test_invalid_citations_and_changed_quotes_are_rejected(proposal):
    with pytest.raises((SelectionRejected, DraftRejected)):
        model_output.accept(reply(proposal), SOURCES)


@pytest.mark.parametrize("mutation", ["truncated", "tools", "duplicate"])
def test_fence_adapter_keeps_completion_and_tool_guards(mutation):
    body = reply({"claims": []}, fenced=True)
    if mutation == "truncated":
        body["choices"][0]["finish_reason"] = "length"
    elif mutation == "tools":
        body["choices"][0]["message"]["tool_calls"] = [{"id": "forbidden"}]
    else:
        body["choices"][0]["message"]["content"] = '```json\n{"claims":[],"claims":[]}\n```'
    with pytest.raises(AnswerOutputRejected):
        model_output.parse(body)


@pytest.mark.parametrize(
    ("text", "citations", "expected"),
    [
        ("记录有限：[1]只列出了概述。", ["[1]"], "记录有限：只列出了概述。"),
        ("[1] 记录了步骤。", ["[1]"], "记录了步骤。"),
        ("记录了步骤。 [1] [2]", ["[1]", "[2]"], "记录了步骤。"),
    ],
)
def test_redundant_boundary_citations_keep_structured_binding(text, citations, expected):
    body = reply({"claims": [{"text": text, "citations": citations}]})
    original = deepcopy(body)
    accepted = model_output.accept(body, SOURCES)
    assert accepted["claims"] == [{"text": expected, "citations": citations}]
    assert accepted["quotations"] == [
        {"citation": s["citation"], "quote": s["snippet"]} for s in SOURCES
    ]
    assert body == original
    # The shared strict contract is unchanged for callers outside this adapter.
    from fiber_bundle.answer_drafts import validate_draft

    with pytest.raises(DraftRejected, match="citation_in_text"):
        validate_draft({**accepted, "claims": [{"text": text, "citations": citations}]}, SOURCES)


@pytest.mark.parametrize(
    ("text", "citations"),
    [
        ("正文。 [99]", ["[99]"]),
        ("正文。 [1]", ["[2]"]),
        ("正文。 [1]", ["[1]", "[2]"]),
        ("正文。 [1]", ["[1]", "[1]"]),
        ("样品[1]发生变化。", ["[1]"]),
        ("数组：[1]mg。", ["[1]"]),
        ("正文。 [ 1 ]", ["[1]"]),
        ("正文。 [[1]]", ["[1]"]),
        ("正文。 \\[1]", ["[1]"]),
        ("[1]", ["[1]"]),
        ("x" * 598 + "。 [1]", ["[1]"]),
    ],
)
def test_citation_adapter_rejects_ambiguous_or_invalid_references(text, citations):
    with pytest.raises((DraftRejected, SelectionRejected)):
        model_output.accept(reply({"claims": [{"text": text, "citations": citations}]}), SOURCES)


def test_citation_adapter_does_not_hide_changed_source_quotes():
    with pytest.raises(SelectionRejected, match="quote_mismatch"):
        model_output.accept(
            reply(
                {
                    "claims": [{"text": "正文。 [1]", "citations": ["[1]"]}],
                    "quotations": [{"citation": "[1]", "quote": "被改过的正文。"}],
                }
            ),
            SOURCES,
        )


def test_invalid_citation_reply_explains_format_failure_without_configuration_hint():
    from fiber_bundle.channels import query_failures, reply_text

    answer = query_failures.answer("answer_draft_rejected_citation_in_text", "answer_validate", {})
    result = reply_text.text({"status": "query_failed", "answer": answer})
    assert "引用格式" in result and "未自动重试" in result
    assert "消息入口" not in result and "模型配置" not in answer
