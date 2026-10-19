"""Batch summaries expose coverage gaps and never turn declared judgments into truth."""

import copy
import json
import subprocess
import sys

import pytest
from draft_eval_support import FIXTURE, proposal

from fiber_bundle.config import InvalidInput
from fiber_bundle.draft_review import digest, prepare, record
from fiber_bundle.draft_review_summary import summarize

FIXED = json.loads(FIXTURE.read_text())


def entry(case, verdict="pass"):
    candidate = proposal(case, case["samples"][0])
    packet = prepare(FIXED, case["id"], candidate)
    assessment = {
        "packet_sha256": packet["packet_sha256"],
        "reviewer": "synthetic-software-test",
        "reviewer_kind": "agent",
        "judgments": [
            {"criterion": i, "verdict": verdict, "reason": f"合成验收理由 {i}"}
            for i in range(1, len(case["review_criteria"]) + 1)
        ],
    }
    return {
        "case_id": case["id"],
        "proposal": candidate,
        "packet": packet,
        "review": record(FIXED, candidate, packet, assessment),
    }


def batch(entries):
    return {"schema": "fiber-draft-review-batch/v1", "entries": entries}


def test_empty_batch_exposes_every_missing_case():
    result = summarize(FIXED, batch([]))
    assert result["expected_cases"] == result["counts"]["missing"] == 16
    assert result["reviewed_cases"] == 0 and result["review_coverage"] == "incomplete"
    assert result["contract_counts"] == {"accepted": 0, "rejected": 0, "not_run": 16}
    assert result["attention_case_ids"] == [c["id"] for c in FIXED["cases"]]


def test_partial_batch_separates_missing_pending_fail_and_uncertain_with_reasons():
    entries = [
        entry(c, verdict)
        for c, verdict in zip(FIXED["cases"], ["pass", "fail", "uncertain", "pass"], strict=False)
    ]
    entries[-1]["review"] = None
    result = summarize(FIXED, batch(entries))
    assert result["counts"] == {"missing": 12, "pending": 1, "pass": 1, "fail": 1, "uncertain": 1}
    assert result["reviewed_cases"] == 3 and result["review_coverage"] == "incomplete"
    assert result["cases"][1]["reviewer_assessment"] == entries[1]["review"]["reviewer_assessment"]
    for state, count in result["counts"].items():
        assert sum(d[state] for d in result["dimensions"].values()) == count
    assert entries[0]["case_id"] not in result["attention_case_ids"]


def test_complete_batch_has_no_automatic_semantic_or_model_quality_pass():
    inputs = batch([entry(c) for c in FIXED["cases"]])
    before = copy.deepcopy(inputs)
    result = summarize(FIXED, inputs)
    assert result == summarize(FIXED, inputs) and inputs == before
    assert result["reviewed_cases"] == 16 and result["review_coverage"] == "complete"
    assert result["counts"]["pass"] == 16 and result["attention_case_ids"] == []
    assert result["independent_review"] == result["semantic_support"] == "not_verified"
    assert result["reviewer_identity"] == "self_reported"
    assert result["model_quality_evaluation"] == "not_assessed"
    assert result["semantic_accuracy"] is None and result["production_promotion"] == "none"
    assert result["summary_sha256"] == digest(
        {k: v for k, v in result.items() if k != "summary_sha256"}
    )


def test_contract_rejection_remains_attention_even_with_reviewer_pass():
    value = entry(FIXED["cases"][0])
    value["proposal"]["quotations"] = []
    value["packet"] = prepare(FIXED, value["case_id"], value["proposal"])
    assessment = value["review"]["reviewer_assessment"]
    assessment["packet_sha256"] = value["packet"]["packet_sha256"]
    value["review"] = record(FIXED, value["proposal"], value["packet"], assessment)
    result = summarize(FIXED, batch([value]))
    assert result["counts"]["pass"] == 1 and result["contract_counts"]["rejected"] == 1
    assert value["case_id"] in result["attention_case_ids"]


@pytest.mark.parametrize(
    "mutation",
    [
        "proposal",
        "case",
        "packet",
        "outcome",
        "reason",
        "record_hash",
        "extra",
        "timestamp",
        "naive_timestamp",
        "forged_outcome_rehashed",
        "identity",
        "cross_case",
    ],
)
def test_stale_or_inconsistent_records_refuse_entire_batch(mutation):
    fixed = copy.deepcopy(FIXED)
    value = entry(fixed["cases"][0])
    review = value["review"]
    if mutation == "proposal":
        value["proposal"]["claims"].append({"text": "Changed claim", "citations": ["[1]"]})
    elif mutation == "case":
        fixed["cases"][0]["review_criteria"][0] = "Changed criterion"
    elif mutation == "packet":
        value["packet"]["contract_observed"] = "verified"
    elif mutation == "outcome":
        review["reviewer_outcome"] = "fail"
    elif mutation == "reason":
        review["reviewer_assessment"]["judgments"][0]["reason"] = "Edited reason"
    elif mutation == "record_hash":
        review["record_sha256"] = "0" * 64
    elif mutation == "extra":
        review["trust"] = "verified"
    elif mutation == "timestamp":
        review["recorded_at"] = "not-a-time"
    elif mutation == "naive_timestamp":
        review["recorded_at"] = "2026-09-28T00:00:00"
    elif mutation == "forged_outcome_rehashed":
        review["reviewer_outcome"] = "fail"
        review["record_sha256"] = digest({k: v for k, v in review.items() if k != "record_sha256"})
    elif mutation == "identity":
        review["independent_review"] = "verified"
    else:
        value["review"] = entry(fixed["cases"][1])["review"]
    with pytest.raises(InvalidInput):
        summarize(fixed, batch([value]))


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "unknown",
        "unknown_field",
        "wrong_schema",
        "missing_field",
        "empty_fixture",
        "duplicate_fixture",
        "malformed_case",
        "too_many_cases",
    ],
)
def test_invalid_batch_or_fixture_rejected(mutation):
    fixed = copy.deepcopy(FIXED)
    value = entry(fixed["cases"][0])
    inputs = batch([value])
    if mutation == "duplicate":
        inputs["entries"].append(copy.deepcopy(value))
    elif mutation == "unknown":
        value["case_id"] = "not-in-fixture"
    elif mutation == "unknown_field":
        inputs["quality"] = "verified"
    elif mutation == "wrong_schema":
        inputs["schema"] = "other"
    elif mutation == "missing_field":
        del value["review"]
    elif mutation == "empty_fixture":
        fixed["cases"] = []
    elif mutation == "duplicate_fixture":
        fixed["cases"].append(copy.deepcopy(fixed["cases"][0]))
    elif mutation == "malformed_case":
        fixed["cases"][-1]["review_criteria"] = []
    else:
        fixed["cases"] *= 7
    with pytest.raises(InvalidInput):
        summarize(fixed, inputs)


def test_cli_summary_preserves_inputs_refuses_overwrite_and_bad_record(tmp_path):
    fixture_file, batch_file, output = [
        tmp_path / f"{name}.json" for name in ("fixture", "batch", "summary")
    ]
    fixture_file.write_text(json.dumps(FIXED))
    value = entry(FIXED["cases"][0], "uncertain")
    batch_file.write_text(json.dumps(batch([value])))
    before = [p.read_bytes() for p in (fixture_file, batch_file)]
    command = [
        sys.executable,
        "-m",
        "fiber_bundle.draft_review_cli",
        "summarize",
        "--fixture",
        str(fixture_file),
        "--batch",
        str(batch_file),
        "--output",
        str(output),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    assert json.loads(completed.stdout)["counts"]["uncertain"] == 1
    assert "合成验收理由" not in completed.stdout
    assert json.loads(output.read_text())["cases"][0]["reviewer_outcome"] == "uncertain"
    assert [p.read_bytes() for p in (fixture_file, batch_file)] == before
    contents = output.read_bytes()
    assert subprocess.run(command, capture_output=True).returncode == 1
    assert contents == output.read_bytes()
    value["review"]["reviewer_outcome"] = "pass"
    batch_file.write_text(json.dumps(batch([value])))
    rejected_output = tmp_path / "rejected.json"
    command[-1] = str(rejected_output)
    failed = subprocess.run(command, capture_output=True, text=True)
    assert failed.returncode == 1 and not rejected_output.exists()
    assert json.loads(failed.stdout) == {"error": "draft_review_rejected"}
