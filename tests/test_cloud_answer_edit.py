import copy
import json

import pytest
from test_cloud_answer_tasks import cloud_task as cloud_task
from test_cloud_answer_tasks import result
from test_local_query_tasks import local_task as local_task
from test_local_query_tasks import route as route
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner

from fiber_bundle import cloud_answer_edit, cloud_answer_tasks, database
from fiber_bundle.config import InvalidInput
from fiber_bundle.query_routing_boundary import cloud_answer_request
from fiber_bundle.query_routing_config import parse

QUOTES = [
    {"citation": "[1]", "quote": "压力 100 psi。\n限定条件：仅测试。"},
    {"citation": "[2]", "quote": "第二引文 <script>data only</script>"},
]


def bound(quotes=QUOTES):
    return {
        "schema": "local-answer/v1",
        "answer": json.dumps({"mode": "extractive", "quotations": quotes}),
    }


def test_prompt_keeps_answer_only_boundary(routing):
    value = bound()
    request = cloud_answer_request(parse(routing), value)
    assert json.loads(request["messages"][1]["content"]) == {"answer": value["answer"]}
    assert set(request) == {"model", "messages", "max_tokens", "stream"}
    assert "not a source verifier" in request["messages"][0]["content"]
    assert "originals are deliberately unavailable" in request["messages"][0]["content"]
    assert "Copy the supplied" in request["messages"][0]["content"]
    assert "No Markdown fences" in request["messages"][0]["content"]


def test_exact_multiline_quotes_render_in_bound_order():
    content = json.dumps({"text": "  未核验草稿 [2] [1]  ", "quotations": QUOTES})
    assert cloud_answer_edit.accept(bound(), content) == (
        "未核验草稿 [2] [1]\n\n[1] 压力 100 psi。\n限定条件：仅测试。"
        "\n\n[2] 第二引文 <script>data only</script>"
    )


@pytest.mark.parametrize("change", ["drop", "reorder", "renumber", "rewrite", "extra", "fields"])
def test_quote_changes_rejected(change):
    quotes = copy.deepcopy(QUOTES)
    if change == "drop":
        quotes.pop()
    elif change == "reorder":
        quotes.reverse()
    elif change == "renumber":
        quotes[0]["citation"] = "[3]"
    elif change == "rewrite":
        quotes[0]["quote"] = quotes[0]["quote"].replace("100", "200")
    elif change == "extra":
        quotes.append({"citation": "[3]", "quote": "invented"})
    else:
        quotes[0]["verified"] = True
    with pytest.raises(InvalidInput, match="^cloud_response_rejected$"):
        cloud_answer_edit.accept(bound(), json.dumps({"text": "draft", "quotations": quotes}))


@pytest.mark.parametrize(
    "content",
    [
        "The quotations were removed because originals were not supplied.",
        '```json\n{"text":"draft","quotations":[]}\n```',
        '{"text":"draft","text":"replacement","quotations":[]}',
        '{"text":NaN,"quotations":[]}',
        "[]",
        '{"text":" ","quotations":[]}',
        '{"text":42,"quotations":[]}',
        '{"text":"draft","quotations":[],"sources":[]}',
        json.dumps({"text": "new source [999]", "quotations": QUOTES}),
        json.dumps({"text": "\ud800", "quotations": QUOTES}),
        json.dumps({"text": "x" * 16384, "quotations": QUOTES}),
    ],
)
def test_invalid_draft_has_content_free_rejection(content):
    with pytest.raises(InvalidInput, match="^cloud_response_rejected$"):
        cloud_answer_edit.accept(bound(), content)


def test_empty_evidence_and_semantic_limit():
    assert (
        cloud_answer_edit.accept(bound([]), '{"text":"Evidence insufficient.","quotations":[]}')
        == "Evidence insufficient."
    )
    # Exact retention cannot certify newly worded assertions.
    draft = json.dumps({"text": "An unverified claim [1].", "quotations": QUOTES})
    assert cloud_answer_edit.accept(bound(), draft).startswith("An unverified claim [1].")


def test_bracketed_number_inside_bound_quote_is_preserved_as_data():
    quotes = [{"citation": "[1]", "quote": "原文标记 [2026] 不是新增来源。"}]
    draft = json.dumps({"text": "原文标记 [2026]，来源 [1]。", "quotations": quotes})
    assert cloud_answer_edit.accept(bound(quotes), draft).endswith(
        "[1] 原文标记 [2026] 不是新增来源。"
    )


@pytest.mark.parametrize("kind", ["plain", "missing", "changed", "new_citation"])
def test_rejection_preserves_settled_cost_and_refuses_delivery_replay(
    config, route, owner, cloud_task, kind
):
    db = database.connect(config.state_root)
    owned = cloud_answer_tasks.claim(config, db, route, owner[0], cloud_task)
    quotes = json.loads(owned.answer["answer"])["quotations"]
    value = {"text": "draft [1]", "quotations": quotes}
    if kind == "missing":
        value["quotations"] = []
    elif kind == "changed":
        value["quotations"][0]["quote"] = "200 psi"
    elif kind == "new_citation":
        value["text"] = "invented [999]"
    content = "Removed quotations." if kind == "plain" else json.dumps(value)
    output = cloud_answer_tasks.finish(config, db, route, owned, result(answer=content))
    assert output["state"] == "failed" and output["error_code"] == "cloud_response_rejected"
    assert tuple(db.execute("SELECT state,charged_nusd FROM model_calls").fetchone()) == (
        "settled",
        18000,
    )
    assert db.execute("SELECT result_text FROM cloud_answer_tasks").fetchone()[0] is None
    with pytest.raises(InvalidInput, match="not_ready"):
        cloud_answer_tasks.deliver(config, db, owner[0], cloud_task)
    with pytest.raises(InvalidInput, match="not_replayable"):
        cloud_answer_tasks.claim(config, db, route, owner[0], cloud_task)
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 1
    db.close()
