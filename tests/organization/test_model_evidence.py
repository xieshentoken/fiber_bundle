import json

import pytest

from fiber_bundle.organization import model_evidence

BODY = "# 资料标题\n固体内部的热量通过温度梯度传递。\n冷却液通过流动将热量带离高温区域。\n"
PROFILE = {"id": "heat", "text": "- 热怎么传出去：TIM、冷板、液冷、材料导热、封装路径。"}


def answer(**changes):
    return json.dumps(
        {
            "moc_id": "heat",
            "evidence": ["固体内部的热量通过温度梯度传递。", "冷却液通过流动将热量带离高温区域。"],
            "scope_quote": "热怎么传出去:TIM、冷板、液冷、材料导热、封装路径。",
            "reason": "正文讨论传热及冷却。",
            **changes,
        },
        ensure_ascii=False,
    )


def test_scope_punctuation_maps_to_original_definition_and_body_offsets():
    result = model_evidence.validate(answer(), BODY, [PROFILE])
    span = result["scope_span"]
    assert result["scope_quote"] == PROFILE["text"][span["start"] : span["end"]]
    assert "：" in result["scope_quote"]
    assert all(BODY[s["start"] : s["end"]] == s["quote"] for s in result["evidence"])


@pytest.mark.parametrize(
    "quote", ["传热 (Heat transfer), 冷却；热阻", "传 热（Heat transfer），冷却;热阻"]
)
def test_scope_only_equivalent_spacing_and_explicit_punctuation(quote):
    profile = {"id": "heat", "text": "传热（Heat transfer），冷却；热阻"}
    result = model_evidence.validate(answer(scope_quote=quote), BODY, [profile])
    assert result["scope_quote"] == profile["text"]


@pytest.mark.parametrize(
    "changed", ["热阻为1.6 K/W", "热阻为15 K/W", "热阻为１.５ K/W", "热容为1.5 K/W"]
)
def test_scope_does_not_normalize_numbers_or_terms(changed):
    with pytest.raises(model_evidence.EvidenceInvalid) as error:
        model_evidence.validate(
            answer(scope_quote=changed), BODY, [{"id": "heat", "text": "热阻为1.5 K/W"}]
        )
    assert error.value.reason == "scope_quote_missing"


def test_scope_keeps_latin_word_boundaries():
    with pytest.raises(model_evidence.EvidenceInvalid):
        model_evidence.validate(
            answer(scope_quote="heatflow cooling"),
            BODY,
            [{"id": "heat", "text": "heat flow cooling"}],
        )


def test_body_sentence_separator_line_break_maps_to_original_source_and_window():
    body = (
        "生产监控采用SPC识别过程波动;\nFDC关注设备和过程异常。\n"
        "另一条独立的正文证据描述制造过程。\n"
    )
    raw = answer(
        evidence=[
            "生产监控采用SPC识别过程波动;FDC关注设备和过程异常。",
            "另一条独立的正文证据描述制造过程。",
        ]
    )
    window = [{"start": 0, "end": len(body)}]
    result = model_evidence.validate(raw, body, [PROFILE], windows=window)
    assert all(body[s["start"] : s["end"]] == s["quote"] for s in result["evidence"])
    assert any(";\nFDC" in s["quote"] for s in result["evidence"])
    with pytest.raises(model_evidence.EvidenceInvalid):
        model_evidence.validate(raw, body, [PROFILE], windows=[{"start": 0, "end": 20}])


@pytest.mark.parametrize(
    "original,changed",
    [
        ("heat flow传递温度梯度", "heatflow传递温度梯度"),
        ("热阻为1.5 K/W保持稳定", "热阻为15 K/W保持稳定"),
        ("指标为1, 000保持稳定", "指标为1,000保持稳定"),
    ],
)
def test_body_spacing_never_erases_words_numbers_or_comma_boundaries(original, changed):
    with pytest.raises(model_evidence.EvidenceInvalid):
        model_evidence.validate(
            answer(evidence=[changed, "独立证据说明其他过程。"]),
            original + "\n独立证据说明其他过程。",
            [PROFILE],
        )


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"moc_id": "unknown"}, "moc_not_allowed"),
        ({"evidence": ["固体内部的热量通过温度梯度传递。"]}, "body_quote_count"),
        ({"evidence": ["固体内部的热量通过温度梯度传递。"] * 2}, "body_quote_overlap"),
        (
            {"evidence": ["这是模型编造的正文内容。", "冷却液通过流动将热量带离高温区域。"]},
            "body_quote_missing",
        ),
        ({"evidence": ["# 资料标题", "冷却液通过流动将热量带离高温区域。"]}, "body_quote_missing"),
        ({"scope_quote": "不存在的知识主题"}, "scope_quote_missing"),
        ({"extra": True}, "format"),
    ],
)
def test_diagnostics_remain_specific_without_source_content(changes, reason):
    with pytest.raises(model_evidence.EvidenceInvalid) as error:
        model_evidence.validate(answer(**changes), BODY, [PROFILE])
    assert str(error.value) == "organization_model_evidence_invalid"
    assert error.value.reason == reason


def test_body_equivalent_punctuation_returns_original_source():
    body = BODY.replace("温度梯度传递。", "温度梯度传递：向低温方向。")
    result = model_evidence.validate(
        answer(
            evidence=[
                "固体内部的热量通过温度梯度传递:向低温方向。",
                "冷却液通过流动将热量带离高温区域。",
            ]
        ),
        body,
        [PROFILE],
    )
    assert "：" in result["evidence"][0]["quote"]
    assert all(body[s["start"] : s["end"]] == s["quote"] for s in result["evidence"])


def test_body_quotes_must_belong_to_the_supplied_window():
    first = BODY.index("固体")
    windows = [{"start": first, "end": BODY.index("\n", first)}]
    with pytest.raises(model_evidence.EvidenceInvalid) as error:
        model_evidence.validate(answer(), BODY, [PROFILE], windows=windows)
    assert error.value.reason == "body_quote_missing"


@pytest.mark.parametrize(
    "raw", ['{"moc_id":null,"moc_id":"heat"}', '{"moc_id":NaN}', "plain prose"]
)
def test_invalid_json_fails_closed(raw):
    with pytest.raises(model_evidence.EvidenceInvalid) as error:
        model_evidence.validate(raw, BODY, [PROFILE])
    assert error.value.reason == "format"
