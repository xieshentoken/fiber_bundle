"""Literal ASR chunks and separately audited glossary normalization; never overwrite raw text."""

import hashlib
import re
import unicodedata
from typing import Any

from .config import InvalidInput
from .media_files import canonical
from .media_glossary_contract import ambiguities, validate
from .media_subtitles import gaps, normalized


def text(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 8000
        or any(unicodedata.category(c) in {"Cc", "Cf"} and c not in "\n\t" for c in value)
    ):
        raise InvalidInput("media_asr_text")
    try:
        value.encode()
    except UnicodeError as e:
        raise InvalidInput("media_asr_unicode") from e
    return value


def chunks(value: object, duration: int) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        raise InvalidInput("media_asr_chunks")
    end = 0
    for row in value:
        if not isinstance(row, dict) or set(row) != {
            "start_ms",
            "end_ms",
            "text",
            "language",
            "state",
        }:
            raise InvalidInput("media_asr_chunk_fields")
        if (
            type(row["start_ms"]) is not int
            or type(row["end_ms"]) is not int
            or (row["start_ms"] != end or not end < row["end_ms"] <= duration)
            or row["state"] not in ("complete", "empty", "failed")
        ):
            raise InvalidInput("media_asr_chunk_bounds")
        text(row["text"])
        text(row["language"])
        if (row["state"] == "complete") != bool(row["text"].strip()):
            raise InvalidInput("media_asr_chunk_state")
        end = row["end_ms"]
    if end != duration or len(canonical(value)) > 256 * 1024:
        raise InvalidInput("media_asr_coverage")
    return value


def convert(raw: list[dict[str, Any]], glossary: dict[str, Any], duration: int) -> dict[str, Any]:
    chunks(raw, duration)
    validate(glossary)
    if ambiguities(glossary):
        raise InvalidInput("media_asr_glossary_ambiguous")
    cues = []
    corrections = []
    issues = []
    for i, row in enumerate(raw):
        if row["state"] != "complete":
            continue
        original = row["text"]
        current = original
        # One pass over original spans, never cascade replacement into a second alias.
        changes = []
        for term in glossary["terms"]:
            for alias in term["aliases"]:
                a = alias["value"]
                pattern = (
                    (r"(?<![A-Za-z0-9_])" + re.escape(a) + r"(?![A-Za-z0-9_])")
                    if a.isascii()
                    else re.escape(a)
                )
                for match in re.finditer(pattern, original, re.IGNORECASE):
                    if alias["mode"] == "contextual" and not any(
                        k.casefold() in original.casefold() for k in term["context_keywords"]
                    ):
                        issues.append({"chunk": i + 1, "term": a, "reason": "context_not_present"})
                        continue
                    changes.append((match.start(), match.end(), term["canonical"]))
        changes.sort()
        if any(changes[j][0] < changes[j - 1][1] for j in range(1, len(changes))):
            issues.append({"chunk": i + 1, "term": "", "reason": "overlapping_aliases"})
            changes = []
        for a, b, replacement in reversed(changes):
            current = current[:a] + replacement + current[b:]
        if current != original:
            corrections.append(
                {
                    "chunk": i + 1,
                    "original": original,
                    "normalized": current,
                    "reason": "confirmed_glossary_alias",
                    "original_sha256": hashlib.sha256(original.encode()).hexdigest(),
                }
            )
        cues.append({"start_ms": row["start_ms"], "end_ms": row["end_ms"], "text": current})
    missing = gaps(cues, duration)
    return {
        "cues": cues,
        "corrections": corrections,
        "ambiguities": issues,
        "uncovered_ranges": missing,
        "raw_sha256": hashlib.sha256(canonical({"chunks": raw})).hexdigest(),
        "normalized_sha256": hashlib.sha256(normalized(cues)).hexdigest() if cues else None,
        "timestamp_basis": "audio_chunk_interval",
        "word_alignment_verified": False,
        "semantic_review_verified": False,
    }
