import json

import pytest

from fiber_bundle.config import InvalidInput
from fiber_bundle.organization import model_review, semantic


def test_quotes_require_two_independent_real_body_passages_and_allowed_scope():
    body = "# 传热\n傅里叶定律描述温度梯度。\n热流密度沿法向传递。\n"
    profiles = [{"id": "heat", "text": "传热基础与仿真"}]
    value = {
        "moc_id": "heat",
        "evidence": ["傅里叶定律描述温度梯度。", "热流密度沿法向传递。"],
        "scope_quote": "传热基础与仿真",
        "reason": "传热是主题，而非交易热度。",
    }
    out = model_review.validate(json.dumps(value), body, profiles)
    assert model_review.validate("```json\n" + json.dumps(value) + "\n```", body, profiles) == out
    marked = {**value, "evidence": ["- " + q for q in value["evidence"]]}
    assert model_review.validate(json.dumps(marked), body, profiles) == out
    assert all(body[p["start"] : p["end"]] == p["quote"] for p in out["evidence"])
    for bad in [
        dict(value, moc_id="other"),
        dict(value, evidence=["模型编造了一个不存在的引文。"] * 2),
        dict(value, evidence=["傅里叶定律描述温度梯度。"] * 2),
        dict(value, scope_quote="不存在的主题"),
        dict(value, confidence=1),
    ]:
        with pytest.raises(InvalidInput, match="evidence_invalid"):
            model_review.validate(json.dumps(bad), body, profiles)


def test_all_body_chunks_reach_the_tail_without_media_loading():
    text = (
        "# Generic\n" + "长正文讨论无关主题。" * 500 + "\n末尾给出导热系数。\n![Image](private.png)"
    )
    chunks = semantic.chunks(text)
    assert chunks[-1]["text"] == "末尾给出导热系数。"
    assert all(c["text"] == text[c["start"] : c["end"]] for c in chunks)
    assert not any("private.png" in c["text"] for c in chunks)


def test_incidental_match_in_one_window_does_not_classify_the_whole_long_note(
    config, store, monkeypatch
):
    from types import SimpleNamespace

    monkeypatch.setattr(
        model_review, "route", lambda *a: SimpleNamespace(digest="test", max_input_tokens=4000)
    )
    mocs = [
        {
            "id": "heat",
            "title": "热管理 MOC",
            "path": "Map/热管理 MOC.md",
            "text": "# 热管理 MOC\n热管理定义与传热基础。",
        }
    ]
    text = "# Generic\n" + "\n".join(
        f"第{i}段独立正文。" + "这段内容讨论其他主题。" * 25 for i in range(30)
    )
    calls = []

    def reply(config, job, selected, messages, key, check):
        data = json.loads(messages[-1]["content"])
        calls.append(data)
        if len(calls) > 1:
            return json.dumps(
                {"moc_id": None, "evidence": [], "scope_quote": "", "reason": "主题不符"}
            )
        body = [p["text"] for p in data["passages"] if not p["text"].startswith("#")]
        return json.dumps(
            {
                "moc_id": "heat",
                "evidence": body[:2],
                "scope_quote": "热管理定义与传热基础。",
                "reason": "只有首段提到传热。",
            }
        )

    monkeypatch.setattr(model_review, "call", reply)
    result = model_review.resolve(
        config,
        {"vault_id": "test"},
        {"title": "Generic", "text": text},
        mocs,
        {"fingerprint": "fixed"},
        lambda: None,
    )
    assert result["body_windows"] > 2 and result["selected"] == [] and result["status"] == "review"
