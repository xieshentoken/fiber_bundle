"""Offline replay of fixed draft examples; labels are fixtures, not semantic judgments.

Run: python tests/draft_eval_support.py [--output PATH]
No model or network calls. Unrecognized prose always requires review.
"""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from fiber_bundle import answer_generation
from fiber_bundle.answer_drafts import DraftRejected
from fiber_bundle.answer_model_output import AnswerOutputRejected

FIXTURE = Path(__file__).parent / "fixtures/m5_draft_eval.json"


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def proposal(case: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "cited_draft",
        "claims": sample["claims"],
        "quotations": [
            {"citation": e["citation"], "quote": e["snippet"]} for e in case["evidence"]
        ],
    }


def evaluate(case: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Exercise real prompt/output/delivery functions without calling a model."""
    sources = case["evidence"]
    response = {
        "question": case["question"],
        "sources": sources,
        "quotations": [{"citation": e["citation"], "quote": e["snippet"]} for e in sources],
        "comparisons": [],
        "cautions": ["合成评估资料，非真实模型产物。"],
    }
    request = {"mode": "cited_draft"}
    prompt = answer_generation.model_request(request, response)
    assert json.loads(prompt["messages"][1]["content"]) == {
        "question": case["question"],
        "quotations": response["quotations"],
    }
    body = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": json.dumps(candidate, ensure_ascii=False),
                },
            }
        ]
    }
    try:
        accepted = answer_generation.accept(request, body, sources)
        answer_generation.deliver(request, accepted, response)
        observed = "accepted"
        assert response["answer"].startswith("模型草稿，语义未核验。")
        assert all(c["semantic_support"] == "not_verified" for c in response["claims"])
        assert response["semantic_sufficiency"] == "not_assessed"
        assert response["conflict_assessment"] == response["freshness_assessment"] == "not_assessed"
        assert response["sources"] == sources
        assert response["quotations"] == candidate["quotations"]
    except (DraftRejected, AnswerOutputRejected) as exc:
        observed = exc.code
    matches = [s for s in case["samples"] if proposal(case, s) == candidate]
    return {
        "case_id": case["id"],
        "case_sha256": digest(case),
        "candidate_sha256": digest(candidate),
        "contract_observed": observed,
        "fixture_sample": matches[0]["id"] if len(matches) == 1 else None,
        "fixture_quality_label": matches[0]["quality_label"] if len(matches) == 1 else "unreviewed",
        "semantic_support": "not_verified",
        "independent_review": "not_run",
    }


def replay(fixture: dict[str, Any]) -> dict[str, Any]:
    assert fixture["schema"] == "fiber-draft-evaluation/v1"
    assert fixture["model_outputs"] is False
    records = []
    for case in fixture["cases"]:
        for sample in case["samples"]:
            result = evaluate(case, proposal(case, sample))
            result["expected_contract"] = sample["expected_contract"]
            result["contract_expectation_matched"] = (
                result["contract_observed"] == sample["expected_contract"]
            )
            records.append(result)
    return {
        "schema": "fiber-draft-evaluation-report/v1",
        "fixture_sha256": digest(fixture),
        "label_provenance": fixture["provenance"],
        "cases": len(fixture["cases"]),
        "samples": len(records),
        "contract_regression": "PASS"
        if all(r["contract_expectation_matched"] for r in records)
        else "FAIL",
        "accepted_counterexamples": sum(
            r["contract_observed"] == "accepted" and r["fixture_quality_label"] == "counterexample"
            for r in records
        ),
        "model_quality_evaluation": "NOT_RUN",
        "semantic_accuracy": None,
        "real_provider_calls": 0,
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = replay(json.loads(FIXTURE.read_text()))
    report = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        # Evidence files are immutable; callers choose a fresh output path.
        with args.output.open("x") as target:
            target.write(report)
    print(json.dumps({k: v for k, v in result.items() if k != "records"}))
    return 0 if result["contract_regression"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
