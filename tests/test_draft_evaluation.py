"""Fixed examples exercise contract boundaries; no automated semantic PASS is produced."""

import copy
import json

import pytest
from draft_eval_support import FIXTURE, evaluate, proposal, replay

FIXED = json.loads(FIXTURE.read_text())
SAMPLES = [(case, sample) for case in FIXED["cases"] for sample in case["samples"]]


@pytest.mark.parametrize("case,sample", SAMPLES, ids=[f"{c['id']}-{s['id']}" for c, s in SAMPLES])
def test_fixed_draft_example(case, sample):
    before = copy.deepcopy(case)
    result = evaluate(case, proposal(case, sample))
    assert result["contract_observed"] == sample["expected_contract"]
    assert result["fixture_quality_label"] == sample["quality_label"]
    assert result["fixture_sample"] == sample["id"]
    assert result["semantic_support"] == "not_verified"
    assert result["independent_review"] == "not_run"
    assert case == before


def test_fixture_has_explicit_rubrics_provenance_and_balanced_examples():
    assert FIXED["provenance"] == "assistant_authored_synthetic_examples_not_independent_gold"
    assert FIXED["model_outputs"] is False
    assert len({c["id"] for c in FIXED["cases"]}) == len(FIXED["cases"]) == 16
    assert {c["dimension"] for c in FIXED["cases"]} == {
        "relevance",
        "conditions",
        "conflict",
        "freshness",
        "abstention",
        "provenance",
    }
    for case in FIXED["cases"]:
        assert case["question"] and case["review_criteria"]
        assert all(isinstance(x, str) and x.strip() for x in case["review_criteria"])
        assert {s["quality_label"] for s in case["samples"]} == {
            "acceptable_example",
            "counterexample",
        }
        assert all(s["rationale"] for s in case["samples"])
        reference = case["samples"][0]
        assert bool(reference["claims"]) == (case["expected_behavior"] != "abstain")


def test_unknown_paraphrase_never_inherits_fixture_quality_label():
    case = next(c for c in FIXED["cases"] if c["id"] == "conditions")
    value = proposal(case, case["samples"][0])
    value = copy.deepcopy(value)
    value["claims"][0]["text"] = "这是尚未审核的新概括，可能正确也可能错误。"
    result = evaluate(case, value)
    assert result["contract_observed"] == "accepted"
    assert result["fixture_sample"] is None
    assert result["fixture_quality_label"] == "unreviewed"
    assert result["independent_review"] == "not_run"


def test_report_separates_known_counterexamples_from_contract_success():
    report = replay(FIXED)
    assert report == replay(FIXED)
    assert report["cases"] == 16 and report["samples"] == 32
    assert report["contract_regression"] == "PASS"
    assert report["accepted_counterexamples"] == 15
    assert report["semantic_accuracy"] is None
    assert report["model_quality_evaluation"] == "NOT_RUN"
    assert report["real_provider_calls"] == 0


def test_broken_contract_expectation_cannot_report_pass():
    fixture = copy.deepcopy(FIXED)
    fixture["cases"][0]["samples"][0]["expected_contract"] = "unexpected_rejection"
    assert replay(fixture)["contract_regression"] == "FAIL"


def test_unknown_citation_does_not_match_an_annotated_example():
    case = FIXED["cases"][0]
    value = copy.deepcopy(proposal(case, case["samples"][1]))
    value["claims"][0]["citations"] = ["[99]"]
    result = evaluate(case, value)
    assert result["contract_observed"] == "answer_draft_rejected_unknown_citation"
    assert result["fixture_quality_label"] == "unreviewed"
