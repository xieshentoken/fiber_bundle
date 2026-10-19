"""Read-only advisory media checks; shape acceptance cannot authorize any effects."""

import hashlib
import json
from typing import Any, Literal

from .config import InvalidInput
from .document_capture import reject_duplicate
from .media_contract import MAX_MANIFEST_BYTES, validate


def _constant(value: str) -> None:
    raise InvalidInput("media_json_constant")


def covered_ms(ranges: list[dict[str, Any]]) -> int:
    """Union ordered overlapping intervals, without changing cue text/order."""
    end = total = 0
    for row in ranges:
        total += max(0, row["end_ms"] - max(end, row["start_ms"]))
        end = max(end, row["end_ms"])
    return total


def inspect(
    raw: bytes,
    *,
    permission: Literal["unverified", "denied"] = "unverified",
    cancelled: bool = False,
) -> dict[str, Any]:
    if (
        not isinstance(permission, str)
        or permission not in {"unverified", "denied"}
        or type(cancelled) is not bool
    ):
        raise InvalidInput("media_scope_state")
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_MANIFEST_BYTES:
        raise InvalidInput("media_manifest_size")
    try:
        job = validate(
            json.loads(
                raw.decode("utf-8"), object_pairs_hook=reject_duplicate, parse_constant=_constant
            )
        )
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidInput("media_json_invalid") from None
    source, transcript, glossary, visual = (
        job[k] for k in ("source", "transcript", "glossary", "visual")
    )
    reasons = []
    duration, declared = source["local_duration_ms"], source["declared_duration_ms"]
    # No tolerance is an advisory M9.0 boundary; never inherit a preview's denominator.
    if declared is None:
        reasons.append("declared_duration_missing")
    elif duration != declared:
        reasons.append("declared_local_duration_mismatch")
    if source["source_state"] != "complete":
        reasons.append("source_coverage_unverified")
    transcript_coverage = covered_ms(transcript["cues"])
    if transcript["kind"] == "none" or transcript_coverage < duration:
        reasons.append("transcript_coverage_incomplete")
    if transcript["failed_ranges"]:
        reasons.append("transcript_failures_present")
    if glossary["unresolved_count"]:
        reasons.append("terminology_unresolved")
    if (
        transcript["kind"] == "asr"
        or glossary["sha256"] is not None
        or glossary["confirmed_sha256"] is not None
    ) and (glossary["sha256"] is None or glossary["sha256"] != glossary["confirmed_sha256"]):
        reasons.append("glossary_confirmation_missing_or_stale")
    visual_coverage = covered_ms(visual["coverage_ranges"])
    if job["evidence_mode"] == "full_evidence" and (
        visual["state"] != "reviewed"
        or visual["frame_count"] == 0
        or visual["unresolved_count"]
        or visual_coverage < duration
    ):
        reasons.append("visual_coverage_or_review_incomplete")
    if permission == "denied":
        reasons.append("current_authorization_denied")
    if cancelled:
        reasons.append("cancelled")
    return {
        "schema": "fiber-media-preflight/v1",
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "source_sha256_claim": source["sha256"],
        "state": "blocked" if reasons else "inspected",
        "blocking_reasons": reasons,
        "metadata_checks_passed": not reasons,
        "evidence_mode": job["evidence_mode"],
        "media_kind": source["media_kind"],
        "transcript_kind": transcript["kind"],
        "declared_duration_ms": declared,
        "local_duration_ms": duration,
        "transcript_covered_ms": transcript_coverage,
        "visual_covered_ms": visual_coverage if job["evidence_mode"] == "full_evidence" else None,
        "audio_only_disclosure": "音频优先整理，未核验画面内容"
        if job["evidence_mode"] == "audio_only"
        else None,
        "source_bytes_verified": False,
        "evidence_files_verified": False,
        "visual_review_verified": False,
        "glossary_confirmation_verified": False,
        "authorization": "NOT_VERIFIED" if permission == "unverified" else "DENIED",
        "publication_ready": False,
        "knowledge_verified": False,
        "cleanup_allowed": False,
        "source_retention": "keep_original"
        if source["ownership"] == "user_original"
        else "keep_requested"
        if source["keep_media"]
        else "keep_until_both_audits_pass",
    }
