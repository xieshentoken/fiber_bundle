import pytest

from fiber_bundle.channels.evidence_passages import ranked


def test_distinct_coverage_beats_repetition_and_longer_application_paragraph():
    mechanism = "金属的电子与声子输运受到散射影响，产生热阻，这是合成机制资料。"
    applications = "金属电子声子" * 25 + "未来应用十分广泛。"
    body = "# 合成\n" + mechanism + "\n\n" + applications
    selected = ranked(body, "金属 声子 电子 热阻", "导致金属热阻抗的本征物理原因是什么？", 2)
    assert selected[0] == (2, 2, mechanism)
    assert all(quote in body for _, _, quote in selected)


def test_causal_intent_breaks_equal_coverage_tie_without_claiming_support():
    prose = "青松压力的测试场景被记录在此，未来可以扩大试验应用。"
    reason = "青松压力变化是由于边界条件改变，因果解释仅适用于本次合成试验条件。"
    assert ranked(prose + "\n\n" + reason, "青松 压力", "为什么青松压力变化", 1)[0][2] == reason


def test_preserves_conditions_in_exact_adjacent_lines():
    body = "# 合成\n青松记录说明压力发生变化。\n原因是外部边界条件改变，仅在本试验成立。"
    result = ranked(body, "青松 原因", "青松压力为什么改变", 1)
    assert result == [(2, 3, body.split("\n", 1)[1])]


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_metadata_code_images_headings_are_not_answer_evidence(fence):
    valid = "青松有效原文说明范围仅限本次合成测试条件。"
    body = (
        f"---\ntitle: 青松原因\n---\n# 青松\n![[青松-image.png]]\n{fence}\n"
        f"青松因为伪代码而被误选的文字。\n{fence}\n{valid}"
    )
    assert ranked(body, "青松", "为什么青松", 3) == [(9, 9, valid)]


def test_multi_passages_do_not_duplicate_overlapping_lines():
    a = "青松压力记录包含完整的适用条件，属于第一段原文。"
    b = "青松压力记录第二段保留不同测量条件，不能合并结论。"
    result = ranked(a + "\n\n" + b, "青松 压力", "介绍青松压力", 2)
    assert len(result) == 2 and {r[2] for r in result} == {a, b}


def test_short_numeric_evidence_is_not_discarded_by_a_length_threshold():
    assert ranked("# 青松\n压力2Pa", "压力", "压力多少", 2) == [(2, 2, "压力2Pa")]
