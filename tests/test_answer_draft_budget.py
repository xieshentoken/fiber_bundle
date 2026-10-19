"""Short-form guidance must not weaken completion, quotation or accounting checks."""

import copy
import json
from dataclasses import replace

import pytest
from test_answer_draft_tasks import submitted as submitted
from test_answer_submissions import enabled as enabled
from test_answer_tasks import invoke, start
from test_execution import policy as policy
from test_session_search import owner as owner

from fiber_bundle import answer_generation, answer_tasks, database
from fiber_bundle.answer_drafts import DraftRejected
from fiber_bundle.answer_model_output import AnswerOutputRejected

SOURCES = [
    {"citation": "[1]", "snippet": "报告一：条件甲下为 100 psi。"},
    {"citation": "[2]", "snippet": "报告二：条件甲下为 80 psi。"},
]
PROPOSAL = {
    "mode": "cited_draft",
    "claims": [
        {"text": "条件甲下两份报告分别为 100 和 80 psi，需核对。", "citations": ["[1]", "[2]"]}
    ],
    "quotations": [{"citation": s["citation"], "quote": s["snippet"]} for s in SOURCES],
}


def body(value, finish="stop"):
    return {
        "choices": [
            {
                "finish_reason": finish,
                "message": {
                    "role": "assistant",
                    "content": json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                },
            }
        ]
    }


def test_compact_claim_keeps_all_quotes_and_unverified_delivery():
    response = {
        "question": "压力？",
        "sources": SOURCES,
        "quotations": PROPOSAL["quotations"],
        "cautions": [],
    }
    prompt = answer_generation.model_request({"mode": "cited_draft"}, response)
    assert json.loads(prompt["messages"][1]["content"])["quotations"] == PROPOSAL["quotations"]
    accepted = answer_generation.accept({"mode": "cited_draft"}, body(PROPOSAL), SOURCES)
    answer_generation.deliver({"mode": "cited_draft"}, accepted, response)
    assert response["quotations"] == PROPOSAL["quotations"]
    assert response["claims"][0]["semantic_support"] == "not_verified"


@pytest.mark.parametrize("mutation", ["unfinished", "drop_quote", "shorten_quote"])
def test_shortening_cannot_bypass_contract(mutation):
    value = copy.deepcopy(PROPOSAL)
    finish = "stop"
    if mutation == "unfinished":
        finish = "length"
    elif mutation == "drop_quote":
        value["quotations"].pop()
    else:
        value["quotations"][1]["quote"] = "80 psi"
    with pytest.raises((DraftRejected, AnswerOutputRejected)):
        answer_generation.accept({"mode": "cited_draft"}, body(value, finish), SOURCES)


def test_length_rejection_retains_charge_and_no_result(submitted, config, policy):
    rid = submitted().json()["run_id"]
    start(config, policy, rid)

    def provider(p, r, payload):
        assert payload["max_tokens"] == 512
        result = body(PROPOSAL, "length")
        result["usage"] = {"prompt_tokens": 200, "completion_tokens": 512}
        return result

    with pytest.raises(AnswerOutputRejected, match="unfinished"):
        invoke(config, policy, rid, provider)
    db = database.connect(config.state_root)
    assert answer_tasks.get(db, rid)["result_json"] is None
    call = db.execute("SELECT state,output_tokens,charged_nusd FROM model_calls").fetchone()
    assert call["state"] == "settled" and call["output_tokens"] == 512 and call["charged_nusd"] > 0
    from fiber_bundle.config import InvalidInput

    with pytest.raises(InvalidInput):
        invoke(config, replace(policy, max_output_tokens=1024), rid, provider)
    assert db.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 1
    db.close()
