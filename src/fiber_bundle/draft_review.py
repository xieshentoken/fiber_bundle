"""Offline, hash-bound review records. Never promotes a model claim to verified knowledge."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError

from .answer_drafts import DraftRejected, validate_draft
from .config import InvalidInput
from .inputs import text
from .payloads import Payload


class ReviewEvidence(Payload):
    citation: Annotated[str, Field(min_length=3, max_length=8)]
    snippet: Annotated[str, Field(min_length=1, max_length=400)]
    source_kind: Annotated[str, Field(min_length=1, max_length=100)]
    source_label: Annotated[str, Field(min_length=1, max_length=200)]


class ReviewCase(Payload):
    id: Annotated[str, Field(min_length=1, max_length=200)]
    dimension: Annotated[str, Field(min_length=1, max_length=100)]
    question: Annotated[str, Field(min_length=1, max_length=2000)]
    evidence: Annotated[list[ReviewEvidence], Field(max_length=20)]
    review_criteria: Annotated[list[str], Field(min_length=1, max_length=20)]
    expected_behavior: Literal["abstain", "qualified_summary"]
    samples: list[dict[str, Any]]


class Judgment(Payload):
    criterion: Annotated[int, Field(ge=1, le=20)]
    verdict: Literal["pass", "fail", "uncertain"]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]


class Assessment(Payload):
    packet_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    reviewer: Annotated[str, Field(min_length=1, max_length=200)]
    reviewer_kind: Literal["human", "agent", "unspecified"]
    judgments: Annotated[list[Judgment], Field(min_length=1, max_length=20)]


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def find_case(fixture: object, case_id: str) -> dict[str, Any]:
    if not isinstance(fixture, dict) or fixture.get("schema") != "fiber-draft-evaluation/v1":
        raise InvalidInput("invalid review fixture")
    cases = fixture.get("cases")
    if not isinstance(cases, list) or any(not isinstance(c, dict) for c in cases):
        raise InvalidInput("invalid review cases")
    ids = [c.get("id") for c in cases]
    if any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
        raise InvalidInput("invalid review case identities")
    matches = [c for c in cases if c["id"] == case_id]
    if len(matches) != 1:
        raise InvalidInput("review case not found")
    try:
        case = ReviewCase.model_validate(matches[0])
    except ValidationError:
        raise InvalidInput("invalid review case") from None
    for criterion in case.review_criteria:
        text(criterion, limit=2000)
    citations = [e.citation for e in case.evidence]
    if len(set(citations)) != len(citations):
        raise InvalidInput("duplicate review evidence")
    return case.model_dump()


def prepare(fixture: object, case_id: str, candidate: object) -> dict[str, Any]:
    case = find_case(fixture, case_id)
    try:
        validate_draft(candidate, case["evidence"])
        observed = "accepted"
    except DraftRejected as exc:
        observed = exc.code
    context = {k: v for k, v in case.items() if k != "samples"}
    packet = {
        "schema": "fiber-draft-review-packet/v1",
        "case_id": case_id,
        "case_sha256": digest(case),
        "candidate_sha256": digest(candidate),
        "context": context,
        "candidate": candidate,
        "contract_observed": observed,
        "review_state": "pending",
        "semantic_support": "not_verified",
        "independent_review": "not_verified",
    }
    packet["packet_sha256"] = digest(packet)
    return packet


def record(
    fixture: object, candidate: object, packet: object, assessment: object
) -> dict[str, Any]:
    if not isinstance(packet, dict) or not isinstance(packet.get("case_id"), str):
        raise InvalidInput("invalid review packet")
    expected = prepare(fixture, packet["case_id"], candidate)
    if digest(packet) != digest(expected):
        raise InvalidInput("review packet or bound inputs changed")
    try:
        reviewed = Assessment.model_validate(assessment)
    except ValidationError:
        raise InvalidInput("invalid review assessment") from None
    if reviewed.packet_sha256 != expected["packet_sha256"]:
        raise InvalidInput("assessment belongs to another review packet")
    text(reviewed.reviewer, limit=200)
    for judgment in reviewed.judgments:
        text(judgment.reason, limit=2000)
    indices = [j.criterion for j in reviewed.judgments]
    required = set(range(1, len(expected["context"]["review_criteria"]) + 1))
    if len(indices) != len(set(indices)) or set(indices) != required:
        raise InvalidInput("every review criterion requires exactly one judgment")
    verdicts = {j.verdict for j in reviewed.judgments}
    outcome = "fail" if "fail" in verdicts else "uncertain" if "uncertain" in verdicts else "pass"
    result = {
        "schema": "fiber-draft-review-record/v1",
        "recorded_at": datetime.now(UTC).isoformat(),
        "packet_sha256": expected["packet_sha256"],
        "case_sha256": expected["case_sha256"],
        "candidate_sha256": expected["candidate_sha256"],
        "case_id": expected["case_id"],
        "review_state": "recorded",
        "contract_observed": expected["contract_observed"],
        "reviewer_assessment": reviewed.model_dump(),
        "reviewer_outcome": outcome,
        "reviewer_identity": "self_reported",
        "independent_review": "not_verified",
        "semantic_support": "not_verified",
        "production_promotion": "none",
    }
    result["record_sha256"] = digest(result)
    return result


def validate_record(
    fixture: object, candidate: object, packet: object, value: object
) -> dict[str, Any]:
    """Recheck saved content and decisions; hashes do not authenticate the reviewer."""
    if not isinstance(value, dict) or not isinstance(value.get("recorded_at"), str):
        raise InvalidInput("invalid saved review")
    try:
        recorded_at = datetime.fromisoformat(value["recorded_at"])
    except ValueError:
        raise InvalidInput("invalid review timestamp") from None
    if recorded_at.utcoffset() is None:
        raise InvalidInput("review timestamp requires timezone")
    expected = record(fixture, candidate, packet, value.get("reviewer_assessment"))
    expected["recorded_at"] = value["recorded_at"]
    expected["record_sha256"] = digest(
        {key: item for key, item in expected.items() if key != "record_sha256"}
    )
    if digest(value) != digest(expected):
        raise InvalidInput("saved review content or outcome changed")
    return expected
