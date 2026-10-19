import json

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
