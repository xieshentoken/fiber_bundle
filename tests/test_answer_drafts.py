"""The draft candidate validates provenance references, never truth or entailment."""

import copy
import json

import pytest
from test_answer_model_output import envelope
from test_answer_submissions import enabled as enabled
from test_execution import policy as policy
from test_session_search import intake
from test_session_search import owner as owner

from fiber_bundle.answer_contract import SelectionRejected, validate_selection
from fiber_bundle.answer_drafts import DraftRejected, validate_draft, validate_draft_response
from fiber_bundle.answer_model_output import AnswerOutputRejected

EVIDENCE = [
    {"citation": "[1]", "snippet": "泵 A 在条件甲下压力 100 psi。"},
    {"citation": "[2]", "snippet": "泵 B 在条件乙下压力 80 psi。"},
]
PROPOSAL = {
    "mode": "cited_draft",
    "claims": [{"text": "两个来源记录了不同条件下的压力。", "citations": ["[1]", "[2]"]}],
    "quotations": [{"citation": e["citation"], "quote": e["snippet"]} for e in EVIDENCE],
}


def test_valid_draft_keeps_both_quotes_and_host_owned_uncertainty():
    result = validate_draft_response(envelope(json.dumps(PROPOSAL)), EVIDENCE)
    assert result["status"] == "unverified_draft"
    assert result["quotations"] == PROPOSAL["quotations"]
    assert result["claims"][0]["citations"] == ["[1]", "[2]"]
    assert result["claims"][0]["semantic_support"] == "not_verified"
    assert result["citation_integrity"] == "verified"
    assert result["semantic_sufficiency"] == result["conflict_assessment"] == "not_assessed"
    assert result["freshness_assessment"] == "not_assessed"
    assert any("语义未核验" in c for c in result["cautions"])


def test_structurally_valid_false_claim_is_never_marked_supported():
    proposal = copy.deepcopy(PROPOSAL)
    proposal["claims"][0]["text"] = "所有泵当前压力均为 900 psi，因此可安全运行。"
    result = validate_draft(proposal, EVIDENCE)
    # This deliberately false claim documents the limit of a structural validator.
    assert result["claims"][0]["semantic_support"] == "not_verified"
    assert result["status"] == "unverified_draft"
    assert result["semantic_sufficiency"] == "not_assessed"


@pytest.mark.parametrize(
    "field,value",
    [
        ("semantic_sufficiency", "verified"),
        ("status", "verified"),
        ("cautions", []),
        ("confidence", 1.0),
        ("answer", "Trust this"),
    ],
)
def test_provider_cannot_set_host_assurances(field, value):
    proposal = {**copy.deepcopy(PROPOSAL), field: value}
    with pytest.raises(DraftRejected, match="answer_draft_rejected_schema"):
        validate_draft(proposal, EVIDENCE)


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("unknown", "unknown_citation"),
        ("duplicate", "duplicate_citation"),
        ("no_citations", "schema"),
        ("omit_quote", "omitted_evidence"),
        ("alter_quote", "quote_mismatch"),
        ("inline", "citation_in_text"),
        ("control", "text"),
        ("blank", "text"),
        ("too_many", "schema"),
        ("too_long", "schema"),
        ("claim_assurance", "schema"),
    ],
)
def test_invalid_draft_is_rejected_without_raw_text_in_error(mutation, reason):
    p = copy.deepcopy(PROPOSAL)
    c = p["claims"][0]
    if mutation == "unknown":
        c["citations"] = ["[99]"]
    elif mutation == "duplicate":
        c["citations"] = ["[1]", "[1]"]
    elif mutation == "no_citations":
        c["citations"] = []
    elif mutation == "omit_quote":
        p["quotations"].pop()
    elif mutation == "alter_quote":
        p["quotations"][0]["quote"] = "PRIVATE altered text"
    elif mutation == "inline":
        c["text"] = "PRIVATE assertion [99]"
    elif mutation == "control":
        c["text"] = "PRIVATE\x00"
    elif mutation == "blank":
        c["text"] = " "
    elif mutation == "too_many":
        p["claims"] *= 9
    elif mutation == "too_long":
        c["text"] = "x" * 601
    else:
        c["semantic_support"] = "verified"
    with pytest.raises(DraftRejected) as caught:
        validate_draft(p, EVIDENCE)
    assert str(caught.value) == "answer_draft_rejected_" + reason
    assert "PRIVATE" not in str(caught.value)


def test_no_evidence_requires_no_claim_and_insufficient_result():
    assert (
        validate_draft({"mode": "cited_draft", "claims": [], "quotations": []}, [])["status"]
        == "insufficient_evidence"
    )
    p = copy.deepcopy(PROPOSAL)
    p["quotations"] = []
    with pytest.raises(DraftRejected, match="claim_without_evidence"):
        validate_draft(p, [])


def test_model_may_abstain_but_cannot_remove_existing_evidence():
    p = {**PROPOSAL, "claims": []}
    result = validate_draft(p, EVIDENCE)
    assert (
        result["status"] == "insufficient_evidence"
        and result["quotations"] == PROPOSAL["quotations"]
    )


@pytest.mark.parametrize(
    "content",
    [
        '{"mode":"cited_draft","mode":"cited_draft"}',
        '{"mode":"cited_draft","claims":NaN}',
        "not JSON",
    ],
)
def test_candidate_uses_existing_strict_provider_decoder(content):
    with pytest.raises(AnswerOutputRejected, match="answer_output_rejected_json"):
        validate_draft_response(envelope(content), EVIDENCE)


def test_legacy_exact_mode_does_not_accept_drafts():
    with pytest.raises(SelectionRejected, match="schema"):
        validate_selection(PROPOSAL, EVIDENCE)


@pytest.mark.parametrize(
    "endpoint",
    [
        "/v1/answers",
        "/v1/answer-tasks",
        "/v1/note-answer-tasks",
        "/v1/scoped-note-answer-tasks",
    ],
)
def test_invalid_or_non_model_mode_is_rejected_in_public_endpoints(http, enabled, owner, endpoint):
    payload = {
        "mode": "cited_draft" if endpoint == "/v1/answers" else "verified_answer",
        "question": "Pressure?",
        "retrieval_query": "pressure",
    }
    if endpoint == "/v1/answer-tasks":
        receipt = intake(http, owner)
        payload.update(request_id="draft", receipt_id=receipt["job_id"], session_id="current")
    elif endpoint == "/v1/note-answer-tasks":
        payload.update(request_id="draft", vault_id=owner[2]["personal"])
    elif endpoint == "/v1/scoped-note-answer-tasks":
        payload.update(request_id="draft", vault_ids=[owner[2]["personal"]])
    else:
        payload.update(vault_ids=[owner[2]["personal"]])
    result = enabled.post(endpoint, headers=owner[1], json=payload)
    assert result.status_code == 422
    assert "claims" not in result.json()
