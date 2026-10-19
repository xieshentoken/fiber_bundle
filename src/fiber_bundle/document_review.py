"""Bound administrator assessments. Neither parsing nor a model can approve publication."""

import sqlite3
import time
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import Field, ValidationError

from .config import InvalidInput
from .inputs import canonical
from .payloads import Payload


class Judgment(Payload):
    unit_id: Annotated[str, Field(min_length=1, max_length=20)]
    verdict: Literal["accept", "reject", "uncertain"]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]


class Assessment(Payload):
    source_sha256: Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
    result_sha256: Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
    source_reviewed: Literal[True]
    visual_reviewed: Literal[True]
    judgments: Annotated[list[Judgment], Field(min_length=1, max_length=300)]


def units(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    if candidate["schema"] == 1:
        return [
            {
                "unit_id": f"P{p['physical_page']}",
                "physical_page": p["physical_page"],
                "ocr_reasons": p["ocr_reasons"],
                "review_reasons": p["review_reasons"],
                "review_state": "not_run",
            }
            for p in candidate["pages"]
        ]
    return [
        {
            "unit_id": p["id"],
            "locator": p["locator"],
            "ocr_reasons": ["ocr_required"] if p["ocr_required"] else [],
            "review_reasons": p["review_reasons"],
            "review_state": "not_run",
        }
        for p in candidate["segments"]
    ]


def checked(candidate: dict[str, Any], value: object) -> Assessment:
    try:
        parsed = Assessment.model_validate(value)
    except ValidationError:
        raise InvalidInput("invalid document assessment") from None
    if (
        parsed.source_sha256 != candidate["source_sha256"]
        or parsed.result_sha256 != candidate["result_sha256"]
    ):
        raise InvalidInput("document assessment bound inputs changed")
    ids = [j.unit_id for j in parsed.judgments]
    if (
        len(ids) != len(set(ids))
        or set(ids) != {u["unit_id"] for u in units(candidate)}
        or any(not j.reason.strip() for j in parsed.judgments)
    ):
        raise InvalidInput("every document unit requires one explicit judgment and reason")
    return parsed


def latest(db: sqlite3.Connection, run_id: str) -> sqlite3.Row | None:
    row = db.execute(
        "SELECT * FROM document_reviews WHERE run_id=? ORDER BY rowid DESC LIMIT 1", (run_id,)
    ).fetchone()
    return row if isinstance(row, sqlite3.Row) else None


def gate(candidate: dict[str, Any], assessment: object | None = None) -> dict[str, Any]:
    pages = units(candidate)
    reasons = ["source_review_not_run", "visual_review_not_run", "candidate_only"]
    state = "not_run"
    if candidate.get("complete") is False:
        reasons.append("source_incomplete")
    if assessment is not None:
        parsed = checked(candidate, assessment)
        state = "administrator_recorded"
        reasons = ["source_incomplete"] if candidate.get("complete") is False else []
        by_id = {j.unit_id: j for j in parsed.judgments}
        for page in pages:
            page["review_state"] = by_id[page["unit_id"]].verdict
            if page["review_state"] != "accept":
                reasons.append("unit_not_accepted")
    ready = not reasons
    return {
        "state": "ready" if ready else "blocked",
        "publication_ready": ready,
        "knowledge_verified": False,
        "review_state": state,
        "reasons": sorted(set(reasons)),
        "pages": pages,
        "independent_gold_review": "not_verified",
    }


def record(
    db: sqlite3.Connection, run_id: str, candidate: dict[str, Any], value: object
) -> dict[str, Any]:
    parsed = checked(candidate, value)
    if db.execute("SELECT 1 FROM document_publications WHERE run_id=?", (run_id,)).fetchone():
        raise InvalidInput("publication review is frozen; use a new source task")
    identity = str(uuid4())
    db.execute(
        "INSERT INTO document_reviews(id,run_id,source_sha256,result_sha256,"
        "assessment_json,actor,created_at) VALUES(?,?,?,?,?,?,?)",
        (
            identity,
            run_id,
            candidate["source_sha256"],
            candidate["result_sha256"],
            canonical(parsed.model_dump()),
            "local_admin",
            time.time(),
        ),
    )
    return {"review_id": identity, "review_gate": gate(candidate, parsed.model_dump())}


def require_publication(candidate: dict[str, Any], assessment: object | None = None) -> None:
    if not gate(candidate, assessment)["publication_ready"]:
        raise InvalidInput("document candidate requires complete source and explicit review")
