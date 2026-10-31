import json

import pytest
from test_channel_natural_query import envelope
from test_channel_natural_query import model_channel as model_channel
from test_message_channels import channel as channel
from test_model_configuration import vault_keychain as vault_keychain

from fiber_bundle import model_gateway
from fiber_bundle.channels import dispatch, model_answer, model_output, query_progress, store
from fiber_bundle.inputs import canonical


@pytest.mark.parametrize("legacy", [False, True])
def test_retrieval_prompt_is_pinned_before_model_admission(
    config, model_channel, monkeypatch, legacy
):
    value, _ = model_channel
    assert dispatch.capture(
        config, value["id"], value["revision"], "version", "ou_synthetic", "查询：青松"
    )
    if legacy:
        with store.opened(config) as db:
            data = json.loads(
                db.execute("SELECT value FROM events WHERE event_id='version'").fetchone()[0]
            )
            data[query_progress.KEY] = query_progress.initial()
            db.execute(
                "UPDATE events SET state='processing',value=? WHERE event_id='version'",
                (canonical(data),),
            )
    prompts = []

    def complete(policy, identity, payload):
        packet = json.loads(payload["messages"][1]["content"])
        prompts.append(payload["messages"][0]["content"])
        return envelope(
            {"claims": [{"text": "原文记录青松资料。", "citations": ["[1]"]}]}
            if "quotations" in packet
            else {"queries": ["青松"]}
        )

    monkeypatch.setattr(model_gateway, "complete", complete)
    dispatch.advance(config, value["id"], value["revision"])
    with store.readonly(config) as db:
        event = db.execute("SELECT state,value FROM events WHERE event_id='version'").fetchone()
    assert event["state"] == "ready"
    assert len(prompts) == 2
    assert prompts[0] == (model_answer.PLAN if legacy else model_answer.PLAN_CURRENT)
    assert json.loads(event["value"]).get("retrieval_plan_version") == (None if legacy else 2)


def test_answer_request_preserves_legacy_bytes_and_labels_new_draft_context():
    response = {
        "question": "问题",
        "quotations": [{"citation": "[1]", "quote": "原文"}],
        "sources": [
            {
                "citation": "[1]",
                "source_kind": "agent_analysis",
                "source_label": "专家Draft",
                "artifact_stage": "draft",
                "review_state": "deferred",
                "knowledge_verified": False,
                "independent_evidence": False,
                "note_path": "private/path",
            }
        ],
    }
    before = model_output.request(response)
    assert before["messages"][0]["content"] == model_output.ANSWER
    assert set(json.loads(before["messages"][1]["content"])) == {"question", "quotations"}
    response["retrieval_recipe"] = {"schema": "channel-retrieval/v3"}
    current = model_output.request(response)
    context = json.loads(current["messages"][1]["content"])["source_context"]
    assert context[0]["knowledge_verified"] is context[0]["independent_evidence"] is False
    assert context[0]["review_state"] == "deferred"
    assert "private/path" not in canonical(current)
    assert json.loads(current["messages"][1]["content"])["quotations"] == response["quotations"]
