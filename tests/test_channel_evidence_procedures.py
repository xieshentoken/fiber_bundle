import pytest

from fiber_bundle.channels.evidence_procedures import ranked


@pytest.mark.parametrize("separator", ["\n", "\r\n"])
def test_steps_keep_exact_newlines_and_route_when_topic_is_only_in_title(separator):
    lines = [
        "---",
        "title: 青松工艺",
        "---",
        "# 青松",
        "## 概述",
        "本报告提取青松工艺流程的详细信息。",
        "## 路线甲",
        "### 制备步骤",
        "",
        "**① 混合**：",
        "按本试验比例混合，球磨2小时。",
        "",
        "**② 加热**：",
        "在惰性气氛中升温至100 °C，保温1小时后冷却。",
        "",
        "## 路线乙",
        "### 工艺原理",
        "低温成型后加热，条件仅适用于乙。",
    ]
    body = separator.join(lines)
    selected = ranked(body, "青松", "整理青松工艺流程", 2)
    assert len(selected) == 2
    assert selected[0][3] == ["青松", "路线甲", "制备步骤"]
    assert "① 混合" in selected[0][2] and "② 加热" in selected[0][2]
    assert selected[1][3] == ["青松", "路线乙", "工艺原理"]
    for start, end, quote, _ in selected:
        assert quote == separator.join(lines[start - 1 : end])
        assert len(quote) <= 400 and quote in body
        assert "本报告" not in quote


def test_metadata_references_code_and_comments_never_supply_procedure_windows():
    body = (
        "---\ntitle: 青松烧结流程\n---\n# 青松\n## 参考文献\n混合加热的步骤。\n"
        "## 工艺步骤\n```\n混合加热的代码。\n```\n"
        "<!--\n混合加热的注释。\n-->\n![[混合加热.png]]\n- [[混合加热]]\n"
        "\n先混合，再加热，最后冷却，仅适用于本合成试验。"
    )
    selected = ranked(body, "青松", "青松流程", 3)
    assert len(selected) == 1
    assert selected[0][2] == "先混合，再加热，最后冷却，仅适用于本合成试验。"


def test_no_procedure_override_for_causal_or_unrelated_questions():
    body = "# 青松\n## 工艺步骤\n先混合，再加热。"
    assert ranked(body, "青松", "为什么青松变化", 2) == []
    assert ranked(body, "无关主题", "整理无关主题流程", 2) == []


def test_bounded_windows_keep_step_labels_and_allow_later_supplement():
    def step(n):
        return f"**步骤{n}**：\n先混合加热，再冷却。" + "保留条件。" * 30

    body = "# 青松\n## 工艺步骤\n\n" + "\n\n".join(step(i) for i in range(6))
    initial = ranked(body, "青松", "青松流程", 2)
    expanded = ranked(body, "青松", "青松流程", 3)
    assert len(initial) == 2 and len(expanded) == 3
    assert initial == expanded[:2]
    assert all(s[2].startswith("**步骤") and len(s[2]) <= 400 and s[2] in body for s in expanded)
    assert all(
        a[1] < b[0] or b[1] < a[0] for i, a in enumerate(expanded) for b in expanded[i + 1 :]
    )
