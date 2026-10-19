import json

import pytest

from fiber_bundle.organization import model_evidence

BODY = "**固体热阻为1.5 K/W：保持稳定。**\n冷却液通过流动将热量带离高温区域。\n"
PROFILE = {"id": "heat", "text": "# 热管理\n- 热阻与温度梯度。\n- 冷却液与流动传热。"}


def reply(**changes):
    return json.dumps(
        {
            "moc_id": "heat",
            "evidence": ["固体热阻为1.5 K/W:保持稳定。", "冷却液通过流动将热量带离高温区域。"],
            "scope_quote": "热阻与温度梯度。冷却液与流动传热。",
            "reason": "主题符合",
            **changes,
        },
        ensure_ascii=False,
    )


def test_emphasis_punctuation_and_list_fragments_keep_exact_source_coordinates():
    result = model_evidence.validate(reply(), BODY, [PROFILE])
    for span in result["evidence"]:
        assert BODY[span["start"] : span["end"]] == span["quote"]
    assert len(result["scope_spans"]) == 2
    for span in result["scope_spans"]:
        assert PROFILE["text"][span["start"] : span["end"]] == span["quote"]


def test_extra_unverified_and_overlapping_quotes_are_isolated_with_two_independent_quotes():
    raw = json.loads(reply())
    raw["evidence"] += ["改写过的内容不作为证据。", raw["evidence"][0]]
    result = model_evidence.validate(json.dumps(raw), BODY, [PROFILE])
    assert result["accepted_quotes"] == 2
    assert [r["reason"] for r in result["rejected_quotes"]] == [
        "body_quote_missing",
        "body_quote_overlap",
    ]


@pytest.mark.parametrize(
    "changed",
    [
        "固体热阻为15 K/W:保持稳定。",
        "固体热容为1.5 K/W:保持稳定。",
        "固体热阻为1.5 W/K:保持稳定。",
        "固体热阻保持稳定。",
    ],
)
def test_words_numbers_units_and_unmarked_omissions_remain_invalid(changed):
    with pytest.raises(model_evidence.EvidenceInvalid):
        model_evidence.validate(
            reply(evidence=[changed, "冷却液通过流动将热量带离高温区域。"]), BODY, [PROFILE]
        )


def test_equivalent_quote_outside_supplied_window_remains_invalid():
    with pytest.raises(model_evidence.EvidenceInvalid):
        model_evidence.validate(
            reply(), BODY, [PROFILE], windows=[{"start": 0, "end": BODY.index("\n")}]
        )


def test_explicit_scope_ellipsis_retains_separate_real_fragments():
    profile = {"id": "heat", "text": "- 热阻与温度梯度。\n- 不被引用的定义。\n- 冷却液与流动传热。"}
    result = model_evidence.validate(
        reply(scope_quote="热阻与温度梯度。……冷却液与流动传热。"), BODY, [profile]
    )
    assert len(result["scope_spans"]) == 2
    with pytest.raises(model_evidence.EvidenceInvalid):
        model_evidence.validate(reply(), BODY, [profile])


@pytest.mark.parametrize("value", [None, "null"])
def test_abstention_is_review_not_unknown_moc(value):
    assert model_evidence.validate(reply(moc_id=value), BODY, [PROFILE]) is None


def test_heading_and_latin_word_boundaries_do_not_become_body_evidence():
    body = "# 热阻与温度梯度。\nheat\nflow保持稳定。\n冷却液通过流动将热量带离高温区域。\n"
    for first in ["热阻与温度梯度。", "heatflow保持稳定。"]:
        with pytest.raises(model_evidence.EvidenceInvalid):
            model_evidence.validate(
                reply(evidence=[first, "冷却液通过流动将热量带离高温区域。"]), body, [PROFILE]
            )
