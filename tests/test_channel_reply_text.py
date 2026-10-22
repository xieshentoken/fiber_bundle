import copy

import pytest

from fiber_bundle.channels import query, reply_text


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("热扩散率是什么？", False),
        ("请根据原文解释热扩散率", False),
        ("请介绍来源于太阳的能量", False),
        ("请解释热扩散率，并注明来源。", True),
        ("请列出引用的笔记", True),
        ("请展示原文", True),
        ("请给我出处", True),
        ("出处是什么？", True),
        ("Explain diffusivity with citations", True),
        ("Cite your sources", True),
        ("请解释，不需要列出来源。", False),
        ("不显示来源", False),
        ("回答时不附引用出处", False),
        ("请标注来源，但不要返回出处。", False),
        ("不要引用，但请注明出处。", True),
        ("Explain without citations", False),
        ("Explain, do not cite sources", False),
    ],
)
def test_sources_are_an_explicit_per_question_request(question, expected):
    assert reply_text.sources_requested(question) is expected


@pytest.fixture
def answer():
    return {
        "question": "热扩散率是什么？",
        "mode": "cited_draft",
        "status": "unverified_draft",
        "answer": "模型草稿，语义未核验。\n材料的响应速度取决于热扩散率。 [1]",
        "claims": [{"text": "材料的响应速度取决于热扩散率。", "citations": ["[1]"]}],
        "sources": [
            {
                "citation": "[1]",
                "title": "材料响应笔记",
                "note_path": "Resource/材料响应.md",
                "snippet": "原文片段不应在没有请求时重复返回。",
            }
        ],
    }


def test_ordinary_model_reply_hides_notes_and_citations_without_changing_evidence(answer):
    before = copy.deepcopy(answer)
    body = query.render(answer, "L2_vault")
    assert "材料的响应速度" in body
    for value in ["[1]", "材料响应笔记", "Resource/", "原文片段", "模型草稿", "知识库："]:
        assert value not in body
    assert answer == before


def test_requested_sources_return_human_locators(answer):
    answer["question"] += "请注明来源。"
    body = query.render(answer, "L2_vault")
    assert "[1]" in body and "材料响应笔记" in body and "Resource/材料响应.md" in body
    assert "原文片段" not in body


def test_original_passages_are_only_added_when_requested(answer):
    answer["question"] += "请展示原文，不用出处。"
    body = query.render(answer, "L2_vault")
    assert "原文片段不应" in body
    assert "Resource/" not in body and "[1]" not in body


def test_evidence_only_reply_is_prose_without_note_locators(answer):
    answer.update(mode="extractive", claims=[])
    body = query.render(answer, "L2_vault")
    assert body == answer["sources"][0]["snippet"]
    assert "Resource/" not in body and "[1]" not in body


def test_expert_text_fallback_hides_ids_and_preserves_missing_image_reason(answer):
    answer["expert_blocks"] = [
        {
            "module_id": "expert:secret",
            "version": "a" * 64,
            "object_ids": ["ko-secret"],
            "evidence_ids": ["ev-secret"],
            "text": "不要重复的专家文字。",
            "behavior": "answer",
            "media": [{"resource_id": "vo-secret", "caption": "vo-secret", "kind": "image"}],
        }
    ]
    before = copy.deepcopy(answer)
    body = query.render(answer, "L2_vault", "text")
    assert "出口仅支持文字" in body and "相关图片" in body
    assert not any(v in body for v in ["expert:", "ko-secret", "ev-secret", "vo-secret", "a" * 64])
    assert "不要重复的专家文字" not in body
    assert answer == before


def test_failed_query_remains_visible_without_protocol_dump():
    body = query.render(
        {"status": "query_failed", "answer": "channel_source_changed; answer_call"}, "L2"
    )
    assert "资料在处理期间发生变化" in body and "消息入口" in body
    assert "channel_source_changed" not in body and "answer_call" not in body
