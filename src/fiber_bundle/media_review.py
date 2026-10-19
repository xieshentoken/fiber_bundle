"""Exact evidence-bound administrator audio/visual assessments and complete-coverage gate."""

import json
import sqlite3
import time
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import Field, ValidationError

from .config import InvalidInput
from .document_review import Judgment
from .media_contract import ranges
from .media_files import canonical
from .media_preflight import covered_ms
from .payloads import Payload


class Assessment(Payload):
    source_sha256: Annotated[str, Field(pattern="^[a-f0-9]{64}$")]
    result_sha256: Annotated[str, Field(pattern="^[a-f0-9]{64}$")]
    glossary_revision: Annotated[int, Field(ge=1, le=100, strict=True)]
    glossary_sha256: Annotated[str, Field(pattern="^[a-f0-9]{64}$")]
    source_reviewed: Literal[True]
    audio_reviewed: Literal[True]
    visual_reviewed: Annotated[bool, Field(strict=True)]
    coverage_ranges: Annotated[list[dict[str, int]], Field(min_length=1, max_length=100)]
    # Full source + up to 100 subtitle cues + six frames in the bounded 60-second slice.
    judgments: Annotated[list[Judgment], Field(min_length=1, max_length=107)]


def units(value: dict[str, Any]) -> list[dict[str, Any]]:
    return (
        [
            {
                "unit_id": "S1",
                "locator": {"start_ms": 0, "end_ms": value["duration_ms"]},
                "text": "Review complete source duration, authorship and audio/visual coverage.",
            }
        ]
        + [
            {
                "unit_id": "T" + str(i + 1),
                "locator": {"start_ms": c["start_ms"], "end_ms": c["end_ms"]},
                "text": c["text"],
            }
            for i, c in enumerate(value["transcript"]["cues"])
        ]
        + [
            {
                "unit_id": "F" + str(i + 1),
                "locator": {"timestamp_ms": f["timestamp_ms"], "sha256": f["sha256"]},
                "text": "Sampled frame; does not prove all visual content.",
            }
            for i, f in enumerate(value["frames"])
        ]
    )


def checked(value: dict[str, Any], assessment: object) -> Assessment:
    try:
        a = Assessment.model_validate(assessment)
    except ValidationError:
        raise InvalidInput("media_review_contract") from None
    if any(
        getattr(a, k) != value[k]
        for k in ("source_sha256", "result_sha256", "glossary_revision", "glossary_sha256")
    ):
        raise InvalidInput("media_review_bound_evidence_changed")
    ranges(a.coverage_ranges, value["duration_ms"])
    if a.visual_reviewed != bool(value["frames"]):
        raise InvalidInput("media_review_visual_mode")
    ids = [j.unit_id for j in a.judgments]
    if (
        len(set(ids)) != len(ids)
        or set(ids) != {u["unit_id"] for u in units(value)}
        or any(not j.reason.strip() for j in a.judgments)
    ):
        raise InvalidInput("media_review_all_units_required")
    return a


def latest(db: sqlite3.Connection, job: str) -> sqlite3.Row | None:
    r = db.execute(
        "SELECT * FROM media_reviews WHERE job_id=? ORDER BY rowid DESC LIMIT 1", (job,)
    ).fetchone()
    return r if isinstance(r, sqlite3.Row) else None


def gate(value: dict[str, Any], assessment: object | None = None) -> dict[str, Any]:
    reasons = []
    if not value["complete"]:
        reasons.append("media_transcript_or_terminology_incomplete")
    if assessment is None:
        reasons.append("media_audio_visual_review_required")
    else:
        a = checked(value, assessment)
        if covered_ms(a.coverage_ranges) != value["duration_ms"]:
            reasons.append("media_review_coverage_incomplete")
        if any(j.verdict != "accept" for j in a.judgments):
            reasons.append("media_review_units_not_accepted")
    return {
        "state": "blocked" if reasons else "ready",
        "publication_ready": not reasons,
        "knowledge_verified": False,
        "blocking_reasons": reasons,
        "human_review_recorded": assessment is not None,
        "cleanup_allowed": False,
    }


def record(
    db: sqlite3.Connection, job: str, value: dict[str, Any], assessment: object
) -> dict[str, Any]:
    a = checked(value, assessment)
    if db.execute("SELECT 1 FROM media_publications WHERE job_id=?", (job,)).fetchone():
        raise InvalidInput("media_publication_review_frozen")
    identity = str(uuid4())
    db.execute(
        "INSERT INTO media_reviews VALUES(?,?,?,?,?,?,?)",
        (
            identity,
            job,
            value["source_sha256"],
            value["result_sha256"],
            value["glossary_revision"],
            canonical(a.model_dump()).decode(),
            time.time(),
        ),
    )
    return {"review_id": identity, "review_gate": gate(value, a.model_dump())}


def assessment(db: sqlite3.Connection, job: str) -> object | None:
    r = latest(db, job)
    return json.loads(r["assessment_json"]) if r else None


def saved(db: sqlite3.Connection, job: str, value: dict[str, Any]) -> dict[str, Any] | None:
    """Read the latest judgment only when it still binds the current evidence."""
    row = latest(db, job)
    if row is None:
        return None
    a = checked(value, json.loads(row["assessment_json"]))
    return {"review_id": row["id"], "recorded_at": row["created_at"], "assessment": a.model_dump()}
