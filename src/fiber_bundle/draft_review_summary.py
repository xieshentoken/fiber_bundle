"""Offline review coverage and issue lists, without inferring model quality."""

from typing import Any

from .config import InvalidInput
from .draft_review import digest, find_case, prepare, validate_record


def summarize(fixture: object, batch: object) -> dict[str, Any]:
    """One candidate per case per batch; omitted cases remain visible as missing."""
    if not isinstance(fixture, dict) or not isinstance(fixture.get("cases"), list):
        raise InvalidInput("invalid review fixture")
    raw_cases = fixture["cases"]
    if not 1 <= len(raw_cases) <= 100:
        raise InvalidInput("review fixture must contain 1 to 100 cases")
    cases = []
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict) or not isinstance(raw_case.get("id"), str):
            raise InvalidInput("invalid review case")
        cases.append(find_case(fixture, raw_case["id"]))
    if (
        not isinstance(batch, dict)
        or set(batch) != {"schema", "entries"}
        or batch["schema"] != "fiber-draft-review-batch/v1"
        or not isinstance(batch["entries"], list)
        or len(batch["entries"]) > len(cases)
    ):
        raise InvalidInput("invalid review batch")
    entries: dict[str, dict[str, Any]] = {}
    known_ids = {case["id"] for case in cases}
    for entry in batch["entries"]:
        if not isinstance(entry, dict) or set(entry) != {"case_id", "proposal", "packet", "review"}:
            raise InvalidInput("invalid review batch entry")
        case_id = entry["case_id"]
        if not isinstance(case_id, str) or case_id not in known_ids or case_id in entries:
            raise InvalidInput("unknown or duplicate batch case")
        entries[case_id] = entry

    rows = []
    counts = dict.fromkeys(("missing", "pending", "pass", "fail", "uncertain"), 0)
    dimensions: dict[str, dict[str, int]] = {}
    contract_counts = {"accepted": 0, "rejected": 0, "not_run": 0}
    attention = []
    for case in cases:
        row: dict[str, Any] = {
            "case_id": case["id"],
            "dimension": case["dimension"],
            "case_sha256": digest(case),
            "review_state": "missing",
            "contract_observed": "not_run",
        }
        state = "missing"
        entry = entries.get(case["id"])
        if entry is not None:
            packet = prepare(fixture, case["id"], entry["proposal"])
            if digest(entry["packet"]) != digest(packet):
                raise InvalidInput("batch packet or bound inputs changed")
            row.update(
                packet_sha256=packet["packet_sha256"],
                candidate_sha256=packet["candidate_sha256"],
                contract_observed=packet["contract_observed"],
                review_state="pending",
            )
            state = "pending"
            if entry["review"] is not None:
                reviewed = validate_record(fixture, entry["proposal"], packet, entry["review"])
                row.update(
                    review_state="recorded",
                    record_sha256=reviewed["record_sha256"],
                    reviewer_outcome=reviewed["reviewer_outcome"],
                    reviewer_assessment=reviewed["reviewer_assessment"],
                )
                state = reviewed["reviewer_outcome"]
        counts[state] += 1
        dimension = dimensions.setdefault(case["dimension"], dict.fromkeys(counts, 0))
        dimension[state] += 1
        contract = row["contract_observed"]
        contract_state = contract if contract in ("accepted", "not_run") else "rejected"
        contract_counts[contract_state] += 1
        if state != "pass" or contract_state != "accepted":
            attention.append(case["id"])
        rows.append(row)

    result = {
        "schema": "fiber-draft-review-summary/v1",
        "fixture_sha256": digest(fixture),
        "batch_sha256": digest(batch),
        "expected_cases": len(cases),
        "reviewed_cases": counts["pass"] + counts["fail"] + counts["uncertain"],
        "review_coverage": "complete"
        if counts["missing"] + counts["pending"] == 0
        else "incomplete",
        "counts": counts,
        "dimensions": dimensions,
        "contract_counts": contract_counts,
        "attention_case_ids": attention,
        "cases": rows,
        "reviewer_identity": "self_reported",
        "independent_review": "not_verified",
        "semantic_support": "not_verified",
        "model_quality_evaluation": "not_assessed",
        "semantic_accuracy": None,
        "production_promotion": "none",
    }
    result["summary_sha256"] = digest(result)
    return result
