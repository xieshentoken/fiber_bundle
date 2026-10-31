from test_channel_natural_query import model_channel as model_channel
from test_message_channels import channel as channel
from test_model_configuration import vault_keychain as vault_keychain

from fiber_bundle import database
from fiber_bundle.channels import legacy_evidence, natural_query, query, store


def test_weak_nonzero_matches_are_broadened_without_generic_condition_search(config, model_channel):
    value, root = model_channel
    (root / "a.md").write_text("# 青松\n青松压力测试标准包含合成要求，但缺少原因说明。")
    (root / "b.md").write_text("# 青松机制\n青松压力的原因是边界条件发生变化，适用范围有限。")
    (root / "unrelated.md").write_text("# 无关标准\n测试标准来自另外领域，与本次主题无关。")
    with database.connect(config.state_root) as db, store.readonly(config) as side:
        current = store.get(side, value["id"])
        packet = natural_query.evidence(
            config, db, current, "为什么青松压力变化", ["青松 测试 标准"]
        )
        paths = {s["note_path"] for s in packet["sources"]}
        assert paths == {"a.md", "b.md"}
        assert all(q.startswith("青松") for q in packet["retrieval_recipe"]["attempted"])
        assert len(packet["retrieval_recipe"]["attempted"]) <= 8
        query.recheck(config, db, current, packet)


def test_legacy_pending_results_keep_original_selector_on_recheck(config, model_channel):
    value, root = model_channel
    (root / "a.md").write_text(
        "# 青松\n青松压力的原因是测试条件变化，保留适用条件。\n\n" + "青松压力" * 30
    )
    with database.connect(config.state_root) as db, store.readonly(config) as side:
        current = store.get(side, value["id"])
        old = legacy_evidence.evidence(config, db, current, "青松压力的原因", ["青松 压力"])
        new = natural_query.evidence(config, db, current, "青松压力的原因", ["青松 压力"])
        assert old["sources"] != new["sources"]
        assert "retrieval_recipe" not in old
        query.recheck(config, db, current, old)
        query.recheck(config, db, current, new)


def test_revalidation_does_not_repeat_failed_discovery_queries(config, model_channel, monkeypatch):
    value, _ = model_channel
    calls = []
    original = query.evidence

    def observed(*args, **kwargs):
        calls.append(args[-1])
        return original(*args, **kwargs)

    monkeypatch.setattr(query, "evidence", observed)
    with database.connect(config.state_root) as db, store.readonly(config) as side:
        current = store.get(side, value["id"])
        packet = natural_query.evidence(
            config, db, current, "介绍青松", ["青松 不存在", "无关主题"]
        )
        assert len(calls) > 1
        calls.clear()
        query.recheck(config, db, current, packet)
        assert calls == packet["retrieval_recipe"]["contributing"] == ["青松"]


def test_procedure_recipe_is_versioned_and_heading_context_is_revalidated(config, model_channel):
    import copy
    import json

    import pytest

    from fiber_bundle.channels import (
        evidence_passages,
        model_answer,
        model_output,
        retrieval_evidence,
    )
    from fiber_bundle.config import InvalidInput

    value, root = model_channel
    text = (
        "# 青松工艺\n## 概述\n青松工艺流程提取于报告。\n## 路线甲\n"
        "### 工艺步骤\n先混合，再加热并冷却，仅适用于甲。"
    )
    (root / "a.md").write_text(text)
    with database.connect(config.state_root) as db, store.readonly(config) as side:
        current = store.get(side, value["id"])
        old = retrieval_evidence.retrieve(
            config, db, current, "青松工艺流程", ["青松"], schema="channel-retrieval/v3"
        )
        before = model_output.request(old)
        expected = evidence_passages.ranked(text, "青松", "青松工艺流程", 2, title="青松工艺")
        assert [s["snippet"] for s in old["sources"] if s["note_path"] == "a.md"] == [
            s[2] for s in expected
        ]
        assert "section_path" not in str(before)
        assert model_output.request(retrieval_evidence.replay(config, db, current, old)) == before
        new = natural_query.evidence(config, db, current, "青松工艺流程", ["青松"])
        assert new["retrieval_recipe"]["schema"] == "channel-retrieval/v4"
        selected = next(s for s in new["sources"] if s["note_path"] == "a.md")
        assert selected["snippet"].startswith("先混合")
        context = json.loads(model_output.request(new)["messages"][1]["content"])["source_context"]
        assert next(s for s in context if s["citation"] == selected["citation"])[
            "section_path"
        ] == ["青松工艺", "路线甲", "工艺步骤"]
        query.recheck(config, db, current, new)
        tampered = copy.deepcopy(new)
        next(s for s in tampered["sources"] if s["note_path"] == "a.md")["section_path"][1] = (
            "路线乙"
        )
        assert model_answer._signature(tampered) != model_answer._signature(new)
        with pytest.raises(InvalidInput, match="channel_source_changed"):
            query.recheck(config, db, current, tampered)
    assert (root / "a.md").read_text() == text
