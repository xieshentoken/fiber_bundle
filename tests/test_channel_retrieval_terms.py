import pytest

from fiber_bundle.channels.retrieval_terms import fallbacks, split


def test_chinese_compound_is_split_using_visible_titles_only():
    titles = ["冷却模块气密检测规范", "模块试验方法", "测试记录与标准"]
    assert split("冷却模块测试标准", titles) == ["冷却模块", "测试", "标准"]
    strict, relaxed = fallbacks(["冷却模块测试标准"], titles)
    assert strict == ["冷却模块 测试 标准"]
    assert relaxed == ["冷却模块 标准", "冷却模块 测试"]


def test_no_vocabulary_does_not_invent_character_or_queries():
    assert fallbacks(["未见过的中文主题"], []) == ([], [])
    assert fallbacks(["argon"], ["argon test"]) == ([], [])


def test_unknown_text_and_technical_identifiers_are_preserved():
    assert split("冷却模块XYZ-42测试", ["冷却模块", "测试"]) == ["冷却模块", "xyz-42", "测试"]
    assert split("冷板试验规范", ["冷板", "规范"]) == ["冷板", "试验", "规范"]


@pytest.mark.parametrize("count", [3, 30])
def test_candidates_are_bounded_and_keep_topic(count):
    strict, relaxed = fallbacks(["主题模块测试标准"] * count, ["主题模块", "测试", "标准"])
    assert len(strict) <= 3 and len(relaxed) <= 3
    assert all(q.startswith("主题模块 ") for q in strict + relaxed)


def test_punctuation_and_expansion_do_not_turn_zero_hits_into_invalid_query():
    assert split("冷板，测试规范", ["冷板", "测试", "规范"]) == ["冷板", "测试", "规范"]
    assert fallbacks(["冷板，测试规范"], ["冷板", "测试", "规范"])[0] == ["冷板 测试 规范"]
    strict, relaxed = fallbacks(["甲" * 200], ["甲" * 12])
    assert len(strict) <= 3 and len(relaxed) <= 3
