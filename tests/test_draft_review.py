"""Review records bind explicit judgments to immutable inputs without upgrading trust."""

import copy
import json
import os
import subprocess
import sys
from unittest.mock import patch

import pytest
from draft_eval_support import FIXTURE, proposal

from fiber_bundle import draft_review as review
from fiber_bundle import draft_review_cli as cli
from fiber_bundle.config import InvalidInput

FIXED = json.loads(FIXTURE.read_text())
CASE = next(c for c in FIXED["cases"] if c["id"] == "conditions")
CANDIDATE = proposal(CASE, CASE["samples"][0])


def assessment(packet, verdict="pass"):
    return {
        "packet_sha256": packet["packet_sha256"],
        "reviewer": "synthetic-test-reviewer",
        "reviewer_kind": "agent",
        "judgments": [
            {"criterion": i, "verdict": verdict, "reason": "合成判断记录，仅用于软件验收。"}
            for i in range(1, len(packet["context"]["review_criteria"]) + 1)
        ],
    }


def test_prepare_pending_bound_and_without_reference_answers():
    packet = review.prepare(FIXED, CASE["id"], CANDIDATE)
    assert packet == review.prepare(FIXED, CASE["id"], CANDIDATE)
    assert packet["case_sha256"] == review.digest(CASE)
    assert packet["candidate_sha256"] == review.digest(CANDIDATE)
    assert packet["review_state"] == "pending"
    assert packet["semantic_support"] == "not_verified"
    assert "samples" not in packet["context"] and "reviewer_assessment" not in packet
    assert packet["packet_sha256"] == review.digest(
        {k: v for k, v in packet.items() if k != "packet_sha256"}
    )


@pytest.mark.parametrize("verdict", ["pass", "fail", "uncertain"])
def test_record_preserves_explicit_decision_without_claiming_semantic_verification(verdict):
    packet = review.prepare(FIXED, CASE["id"], CANDIDATE)
    value = review.record(FIXED, CANDIDATE, packet, assessment(packet, verdict))
    assert value["review_state"] == "recorded" and value["reviewer_outcome"] == verdict
    assert value["reviewer_identity"] == "self_reported"
    assert value["independent_review"] == value["semantic_support"] == "not_verified"
    assert value["production_promotion"] == "none"
    assert value["record_sha256"] == review.digest(
        {k: v for k, v in value.items() if k != "record_sha256"}
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "candidate",
        "criteria",
        "evidence",
        "packet_context",
        "packet_hash",
        "contract_result",
        "assessment_hash",
    ],
)
def test_changed_inputs_or_tampered_packet_refused(mutation):
    fixed, candidate = copy.deepcopy(FIXED), copy.deepcopy(CANDIDATE)
    packet = review.prepare(fixed, CASE["id"], candidate)
    value = assessment(packet)
    case = next(c for c in fixed["cases"] if c["id"] == CASE["id"])
    if mutation == "candidate":
        candidate["claims"][0]["text"] = "另一份回答"
    elif mutation == "criteria":
        case["review_criteria"][0] = "新的审核标准"
    elif mutation == "evidence":
        case["evidence"][0]["snippet"] = "更改的原文"
    elif mutation == "packet_context":
        packet["context"]["question"] = "不同的问题"
    elif mutation == "packet_hash":
        packet["packet_sha256"] = "0" * 64
    elif mutation == "contract_result":
        packet["contract_observed"] = "verified"
    else:
        value["packet_sha256"] = "0" * 64
    with pytest.raises(InvalidInput):
        review.record(fixed, candidate, packet, value)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "extra",
        "blank_reason",
        "blank_reviewer",
        "unknown_verdict",
        "assurance",
    ],
)
def test_incomplete_or_invalid_assessment_refused(mutation):
    packet = review.prepare(FIXED, CASE["id"], CANDIDATE)
    value = assessment(packet)
    if mutation == "missing":
        value["judgments"].pop()
    elif mutation == "duplicate":
        value["judgments"][1] = value["judgments"][0]
    elif mutation == "extra":
        value["judgments"].append({"criterion": 20, "verdict": "pass", "reason": "extra"})
    elif mutation == "blank_reason":
        value["judgments"][0]["reason"] = " "
    elif mutation == "blank_reviewer":
        value["reviewer"] = " "
    elif mutation == "unknown_verdict":
        value["judgments"][0]["verdict"] = "verified"
    else:
        value["semantic_support"] = "verified"
    with pytest.raises(InvalidInput):
        review.record(FIXED, CANDIDATE, packet, value)


def test_structural_rejection_and_self_reported_human_pass_are_separate():
    candidate = copy.deepcopy(CANDIDATE)
    candidate["quotations"] = []
    packet = review.prepare(FIXED, CASE["id"], candidate)
    value = assessment(packet)
    value["reviewer_kind"] = "human"
    result = review.record(FIXED, candidate, packet, value)
    assert result["contract_observed"] == "answer_draft_rejected_omitted_evidence"
    assert result["reviewer_outcome"] == "pass"
    assert (
        result["independent_review"] == "not_verified" and result["production_promotion"] == "none"
    )


def test_known_false_but_structurally_valid_answer_can_receive_explicit_negative_review():
    candidate = proposal(CASE, CASE["samples"][1])
    packet = review.prepare(FIXED, CASE["id"], candidate)
    value = assessment(packet)
    value["judgments"][1].update(
        verdict="fail", reason="原文明确未测试其他条件，概括却声称全部工况。"
    )
    record = review.record(FIXED, candidate, packet, value)
    assert record["contract_observed"] == "accepted"
    assert record["reviewer_outcome"] == "fail"


@pytest.mark.parametrize("raw", ['{"a":1,"a":2}', '{"a":NaN}', "not JSON"])
def test_cli_strict_json_reader(raw, tmp_path):
    path = tmp_path / "input.json"
    path.write_text(raw)
    with pytest.raises(ValueError):
        cli.read_json(path)


def test_input_size_bound_and_exclusive_private_output(tmp_path):
    path = tmp_path / "input"
    path.write_bytes(b" " * (cli.MAX_BYTES + 1))
    with pytest.raises(InvalidInput):
        cli.read_json(path)
    output = tmp_path / "record.json"
    cli.write_new(output, {"safe": "data"})
    assert os.stat(output).st_mode & 0o777 == 0o600
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        cli.write_new(output, {"replacement": True})
    link = tmp_path / "linked.json"
    link.symlink_to(output)
    with pytest.raises(OSError):
        cli.write_new(link, {})
    assert output.read_bytes() == before


def test_real_cli_prepare_then_record_does_not_print_contents(tmp_path):
    fixed = tmp_path / "fixture.json"
    fixed.write_text(json.dumps(FIXED))
    candidate = tmp_path / "proposal.json"
    candidate.write_text(json.dumps(CANDIDATE))
    packet_file, result_file = tmp_path / "packet.json", tmp_path / "record.json"
    base = [sys.executable, "-m", "fiber_bundle.draft_review_cli"]
    common = ["--fixture", str(fixed), "--proposal", str(candidate)]
    made = subprocess.run(
        [*base, "prepare", *common, "--case-id", CASE["id"], "--output", str(packet_file)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(made.stdout)["review_state"] == "pending"
    packet = json.loads(packet_file.read_text())
    assessed = tmp_path / "assessment.json"
    assessed.write_text(json.dumps(assessment(packet, "uncertain")))
    command = [
        *base,
        "record",
        *common,
        "--packet",
        str(packet_file),
        "--assessment",
        str(assessed),
        "--output",
        str(result_file),
    ]
    recorded = subprocess.run(command, capture_output=True, text=True, check=True)
    assert json.loads(recorded.stdout)["review_state"] == "recorded"
    assert json.loads(result_file.read_text())["reviewer_outcome"] == "uncertain"
    before = result_file.read_bytes()
    rejected = subprocess.run(command, capture_output=True, text=True)
    assert rejected.returncode == 1 and "draft_review_rejected" in rejected.stdout
    assert result_file.read_bytes() == before
    assert CANDIDATE["claims"][0]["text"] not in made.stdout + recorded.stdout + rejected.stdout


def test_cli_error_is_content_free(tmp_path, capsys):
    secret = tmp_path / "synthetic.json"
    secret.write_text("PRIVATE-TEST-CONTENT")
    with patch(
        "sys.argv",
        [
            "review",
            "prepare",
            "--fixture",
            str(secret),
            "--proposal",
            str(secret),
            "--case-id",
            "x",
            "--output",
            str(tmp_path / "out"),
        ],
    ):
        assert cli.main() == 1
    assert capsys.readouterr().out.strip() == '{"error": "draft_review_rejected"}'
    assert not (tmp_path / "out").exists()
