import asyncio
import json

import pytest
from test_message_channels import bind
from test_message_channels import channel as channel
from test_model_configuration import spec
from test_model_configuration import vault_keychain as vault_keychain

from fiber_bundle import database, model_budgets, model_gateway
from fiber_bundle.channels import configuration, dispatch, natural_query, query, store
from fiber_bundle.config import InvalidInput
from fiber_bundle.model_configuration import store as models
from fiber_bundle.model_configuration.contracts import Change


@pytest.fixture
def model_channel(config, channel, vault_keychain, monkeypatch):
    value, root = channel
    with database.connect(config.state_root) as db:
        model = models.save(config, db, spec())
        models.change(db, model["id"], Change(action="default", expected_revision=1))
    with store.opened(config) as db:
        old = store.get(db, value["id"])
        settings = {
            **old["value"],
            "answer_mode": "model",
            "natural_questions": True,
            "policy_version": 2,
        }
    with database.connect(config.state_root) as db:
        value = configuration.save(
            config,
            db,
            configuration.Save(
                id=value["id"],
                expected_revision=value["revision"],
                settings=configuration.Settings.model_validate(settings),
            ),
        )
    bind(config, value)
    monkeypatch.setattr(model_gateway, "prepare", lambda policy, payload: payload)
    return value, root


def envelope(value):
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps(value, ensure_ascii=False)},
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    }


def result(config, value):
    with store.readonly(config) as db:
        row = db.execute(
            "SELECT * FROM events WHERE channel_id=? ORDER BY rowid DESC LIMIT 1", (value["id"],)
        ).fetchone()
        return row["state"], json.loads(row["value"])


@pytest.mark.parametrize(
    "body",
    [
        "压力有什么结论？",
        "请概括青松资料并列出来源",
        "怎么理解压力",
        "介绍青松",
        "列出常见材料的热导率",
        "列举常见材料的热导率",
        "给出常见材料的热导率",
        "罗列常见材料的热导率",
        "列出常见材料的热导率。",
    ],
)
def test_natural_questions_are_opt_in(body):
    assert query.question(body) is None
    assert query.question(body, True) == body


@pytest.mark.parametrize(
    "body",
    [
        "/归类",
        "abort",
        "保存为一份",
        "提取这本书",
        "https://example.com/?q=什么",
        "转发聊天记录：怎么做？",
        "这是一段待保存的资料",
        "文档 新建",
        "保存：列出常见材料的热导率",
        "入库：列举常见材料的热导率",
        "归档：给出常见材料的热导率",
        "提取：列出常见材料的热导率",
        "列出 https://example.com/article 中的热导率",
        "给出 www.example.com/article 中的热导率",
    ],
)
def test_material_and_control_messages_keep_existing_route(body):
    assert query.question(body, True) is None


def test_legacy_prefix_and_invalid_plan_are_preserved():
    assert query.question("查询本库：压力", True) == "压力"
    for bad in [{"queries": []}, {"queries": ["x"] * 4}, {"queries": [""]}, {"query": "pressure"}]:
        with pytest.raises((ValueError, InvalidInput)):
            natural_query.queries(bad)


def test_grouped_synonyms_relax_locally_only_after_empty_exact_search(config, model_channel):
    value, _ = model_channel
    with database.connect(config.state_root) as db, store.readonly(config) as side:
        current = store.get(side, value["id"])
        result = natural_query.evidence(config, db, current, "请介绍青松", ["青松 不存在的近义词"])
        assert result["sources"] and result["retrieval_queries"] == ["青松"]
        query.recheck(config, db, current, result)


def test_natural_answer_retrieves_prose_instead_of_metadata_or_image_paths(config, model_channel):
    value, root = model_channel
    body = "青松压力实验显示，在所述条件下压力保持2.4 MPa，结果仅适用于本次合成测试。"
    (root / "a.md").write_text(
        "---\ntitle: 青松\ntags: [青松]\n---\n# 青松\n![[青松-image.png]]\n" + body
    )
    with database.connect(config.state_root) as db, store.readonly(config) as side:
        current = store.get(side, value["id"])
        packet = natural_query.evidence(config, db, current, "介绍青松", ["青松"])
        assert packet["sources"][0]["snippet"] == body
        assert packet["sources"][0]["line"] == 7
        query.recheck(config, db, current, packet)


def test_noncontributing_queries_do_not_change_frozen_passage(config, model_channel):
    value, root = model_channel
    body = "青松实验记录只适用于本次合成测试，原文在检索和复核期间没有修改。"
    (root / "a.md").write_text(
        "# 青松\n" + body + "\n\n压力压力压力测量描述来自同一篇笔记的另一个段落，"
        "不应替换首个主题的证据。"
    )
    with database.connect(config.state_root) as db, store.readonly(config) as side:
        current = store.get(side, value["id"])
        packet = natural_query.evidence(config, db, current, "介绍青松", ["青松", "压力"])
        assert packet["retrieval_queries"] == ["青松"]
        query.recheck(config, db, current, packet)
        assert packet["sources"][0]["snippet"] == body


def test_full_question_uses_two_calls_and_shared_budget_once(config, model_channel, monkeypatch):
    value, _ = model_channel
    calls = []

    def complete(policy, identity, payload):
        calls.append(payload)
        if len(calls) == 1:
            assert "请概括青松资料" in payload["messages"][1]["content"]
            return envelope({"queries": ["青松", "压力"]})
        content = json.loads(payload["messages"][1]["content"])
        quotes = content["quotations"]
        return envelope(
            {
                "mode": "cited_draft",
                "claims": [
                    {"text": "资料记录压力为2.4 MPa。", "citations": [quotes[0]["citation"]]}
                ],
                "quotations": quotes,
            }
        )

    monkeypatch.setattr(model_gateway, "complete", complete)
    assert not dispatch.capture(
        config, value["id"], value["revision"], "foreign", "ou_other", "请概括青松资料"
    )
    assert dispatch.capture(
        config, value["id"], value["revision"], "one", "ou_synthetic", "请概括青松资料"
    )
    assert not dispatch.capture(
        config, value["id"], value["revision"], "one", "ou_synthetic", "请概括青松资料"
    )
    dispatch.advance(config, value["id"], value["revision"])
    state, data = result(config, value)
    assert state == "ready" and data["result"]["model_used"]
    assert len(data["result"]["sources"]) == 1
    assert "2.4 MPa" in data["result"]["answer"]
    with database.connect(config.state_root) as db:
        assert model_budgets.inspect(db)[0]["used"] == 300
        assert (
            db.execute(
                "SELECT count(*) FROM model_configuration_calls WHERE state='succeeded'"
            ).fetchone()[0]
            == 2
        )
    sent = []

    async def sender(owner, body, context, guard):
        guard()
        sent.append(body)
        return True

    assert asyncio.run(dispatch.deliver(config, value["id"], value["revision"], sender)) == "sent"
    assert "2.4 MPa" in sent[0] and "a.md" not in sent[0]
    dispatch.advance(config, value["id"], value["revision"])
    assert len(calls) == 2


def test_missing_evidence_does_not_call_answer_model(config, model_channel, monkeypatch):
    value, _ = model_channel
    calls = []
    monkeypatch.setattr(
        model_gateway,
        "complete",
        lambda *args: calls.append(1) or envelope({"queries": ["不存在的主题"]}),
    )
    dispatch.capture(
        config, value["id"], value["revision"], "one", "ou_synthetic", "介绍不存在的主题"
    )
    dispatch.advance(config, value["id"], value["revision"])
    assert len(calls) == 1
    assert result(config, value)[1]["result"]["status"] == "insufficient_evidence"


def test_material_conductivity_request_completes_answer_without_polite_prefix(
    config, model_channel, monkeypatch
):
    value, root = model_channel
    body = "列出常见材料的热导率"
    statement = "以下热导率仅为合成测试数据：材料甲10 W/(m·K)，材料乙20 W/(m·K)。"
    (root / "a.md").write_text("# 常见材料\n" + statement)
    calls = []

    def complete(policy, identity, payload):
        calls.append(payload)
        content = json.loads(payload["messages"][1]["content"])
        assert content["question"] == body
        if len(calls) == 1:
            return envelope({"queries": ["热导率"]})
        assert content["quotations"][0]["quote"] == statement
        return envelope({"claims": [{"text": statement, "citations": ["[1]"]}]})

    monkeypatch.setattr(model_gateway, "complete", complete)
    assert dispatch.capture(
        config, value["id"], value["revision"], "conductivity", "ou_synthetic", body
    )
    dispatch.advance(config, value["id"], value["revision"])
    state, data = result(config, value)
    assert state == "ready" and data["result"]["citation_integrity"] == "verified"
    assert data["result"]["claims"][0]["text"] == statement
    assert not dispatch.capture(
        config, value["id"], value["revision"], "conductivity", "ou_synthetic", body
    )
    dispatch.advance(config, value["id"], value["revision"])
    assert len(calls) == 2
    assert statement in query.render(data["result"], "本库")


def test_unknown_model_call_returns_safe_failure_without_replay(config, model_channel, monkeypatch):
    value, _ = model_channel
    calls = []

    def broken(*args):
        calls.append(1)
        raise TimeoutError("SECRET_PROVIDER_DETAIL")

    monkeypatch.setattr(model_gateway, "complete", broken)
    dispatch.capture(config, value["id"], value["revision"], "one", "ou_synthetic", "介绍青松")
    dispatch.advance(config, value["id"], value["revision"])
    dispatch.advance(config, value["id"], value["revision"])
    state, data = result(config, value)
    assert state == "ready" and data["result"]["status"] == "query_failed"
    assert "SECRET" not in json.dumps(data) and len(calls) == 1
    with database.connect(config.state_root) as db:
        assert model_budgets.inspect(db)[0]["used"] > 0


def test_source_change_during_answer_blocks_stale_output(config, model_channel, monkeypatch):
    value, root = model_channel
    calls = []

    def complete(policy, identity, payload):
        calls.append(1)
        if len(calls) == 1:
            return envelope({"queries": ["青松"]})
        (root / "a.md").write_text("# 青松\n用户刚刚修改为不同内容。")
        return envelope({"mode": "cited_draft", "claims": [], "quotations": []})

    monkeypatch.setattr(model_gateway, "complete", complete)
    dispatch.capture(config, value["id"], value["revision"], "one", "ou_synthetic", "介绍青松")
    dispatch.advance(config, value["id"], value["revision"])
    _, data = result(config, value)
    assert data["error_code"] == "channel_source_changed"
    assert "2.4 MPa" not in data["result"]["answer"]


def test_restart_does_not_repeat_interrupted_model_admission(config, model_channel, monkeypatch):
    value, _ = model_channel
    dispatch.capture(config, value["id"], value["revision"], "one", "ou_synthetic", "介绍青松")
    with store.opened(config) as db:
        db.execute("UPDATE events SET state='processing'")
    monkeypatch.setattr(model_gateway, "complete", lambda *a: pytest.fail("replayed"))
    dispatch.advance(config, value["id"], value["revision"])
    assert result(config, value)[1]["error_code"] == "channel_model_result_unknown"


def test_invalid_model_quote_retains_paid_response_and_specific_failure(
    config, model_channel, monkeypatch
):
    value, _ = model_channel
    calls = []

    def complete(*args):
        calls.append(1)
        return envelope(
            {"queries": ["青松"]}
            if len(calls) == 1
            else {
                "claims": [],
                "quotations": [{"citation": "[1]", "quote": "并非原文"}],
            }
        )

    monkeypatch.setattr(model_gateway, "complete", complete)
    dispatch.capture(config, value["id"], value["revision"], "one", "ou_synthetic", "介绍青松")
    dispatch.advance(config, value["id"], value["revision"])
    _, data = result(config, value)
    assert data["error_code"] == "answer_selection_quote_mismatch"
    assert data["failure"]["phase"] == "answer_validate"
    assert data["retrieval_queries"] == ["青松"]
    cached = list((config.state_root / "channel-query-results").glob("*.json"))
    assert len(cached) == 2
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in cached)
    dispatch.advance(config, value["id"], value["revision"])
    assert len(calls) == 2
