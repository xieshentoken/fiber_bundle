#!/usr/bin/env python3
"""Normalize glossary terms while preserving raw transcript evidence."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from common import (
    Cue,
    format_timestamp,
    parse_srt,
    parse_text_lines,
    read_json,
    render_srt,
    sha256_json,
    update_pipeline_state,
    write_jsonl,
)


AUTHORIZED = {"public", "authenticated", "member_authorized"}


def alias_pattern(value: str) -> re.Pattern[str]:
    escaped = re.escape(value)
    if value and all(character.isascii() for character in value) and re.match(r"^\w+$", value):
        escaped = rf"(?<!\w){escaped}(?!\w)"
    return re.compile(escaped, flags=re.IGNORECASE)


def window_text(cues: list[Cue], index: int) -> str:
    start = max(0, index - 1)
    end = min(len(cues), index + 2)
    return " ".join(cue.text for cue in cues[start:end]).casefold()


def normalize_cues(
    cues: list[Cue], terms: list[dict[str, Any]]
) -> tuple[list[Cue], list[dict[str, Any]]]:
    normalized: list[Cue] = []
    corrections: list[dict[str, Any]] = []
    correction_number = 1

    for cue_position, cue in enumerate(cues):
        text = cue.text
        context = window_text(cues, cue_position)
        for term in terms:
            canonical = term["canonical"]
            keywords = [value.casefold() for value in term.get("context_keywords", []) if value]
            for alias in term.get("aliases", []):
                value = alias.get("value", "")
                mode = alias.get("mode", "contextual")
                if not value:
                    continue
                pattern = alias_pattern(value)
                matches = list(pattern.finditer(text))
                if not matches:
                    continue
                context_hit = bool(keywords) and any(keyword in context for keyword in keywords)
                applied = mode == "safe" or (mode == "contextual" and context_hit)
                reason = "safe_alias" if mode == "safe" else (
                    "context_keyword" if context_hit else "requires_review"
                )
                for match in matches:
                    corrections.append(
                        {
                            "id": f"T{correction_number:04d}",
                            "cue_index": cue.index,
                            "start": cue.start,
                            "end": cue.end,
                            "observed": match.group(0),
                            "canonical": canonical,
                            "mode": mode,
                            "applied": applied,
                            "reason": reason,
                            "context_excerpt": context[:500],
                        }
                    )
                    correction_number += 1
                if applied:
                    text = pattern.sub(canonical, text)
        normalized.append(Cue(cue.index, cue.start, cue.end, text))
    return normalized, corrections


def render_ambiguities(rows: list[dict[str, Any]]) -> str:
    pending = [row for row in rows if not row["applied"]]
    lines = ["# Terminology ambiguities", ""]
    if not pending:
        lines.append("No unresolved terminology candidates.")
        return "\n".join(lines) + "\n"
    lines.append("Review these candidates before composing the final note.")
    lines.append("")
    for row in pending:
        timestamp = format_timestamp(row.get("start"), separator=".")
        lines.extend(
            [
                f"## {row['id']} at {timestamp}",
                "",
                f"- Observed: `{row['observed']}`",
                f"- Candidate: `{row['canonical']}`",
                f"- Reason: {row['reason']}",
                f"- Context: {row['context_excerpt']}",
                "",
            ]
        )
    return "\n".join(lines)


def normalize_transcript_file(job_dir: Path, input_path: Path, kind: str) -> Path:
    glossary = read_json(job_dir / "terms" / "glossary.json")
    access = read_json(job_dir / "metadata" / "access.json")
    if access.get("status") not in AUTHORIZED:
        raise ValueError("Browser access must be verified before transcript processing")
    if not glossary.get("confirmed"):
        raise ValueError("The glossary must be explicitly confirmed before transcript processing")
    if glossary.get("confirmed_hash") != sha256_json(glossary.get("terms", [])):
        raise ValueError("The glossary changed after confirmation; confirm it again")

    source_text = input_path.read_text(encoding="utf-8-sig")
    is_srt = input_path.suffix.lower() == ".srt" or "-->" in source_text
    cues = parse_srt(source_text) if is_srt else parse_text_lines(source_text)
    if not cues:
        raise ValueError(f"No transcript cues were found in {input_path}")
    normalized, corrections = normalize_cues(cues, glossary.get("terms", []))

    raw_suffix = ".srt" if is_srt else ".txt"
    raw_path = job_dir / "transcript" / f"{kind}_raw{raw_suffix}"
    normalized_path = job_dir / "transcript" / f"{kind}_normalized{raw_suffix}"
    raw_path.write_text(source_text, encoding="utf-8")
    if is_srt:
        normalized_path.write_text(render_srt(normalized), encoding="utf-8")
    else:
        normalized_path.write_text(
            "\n".join(cue.text for cue in normalized) + "\n", encoding="utf-8"
        )
    write_jsonl(job_dir / "transcript" / "term_corrections.jsonl", corrections)
    (job_dir / "review" / "term_ambiguities.md").write_text(
        render_ambiguities(corrections), encoding="utf-8"
    )
    unresolved = sum(1 for row in corrections if not row["applied"])
    update_pipeline_state(
        job_dir,
        "transcript",
        "needs_review" if unresolved else "complete",
        f"{len(cues)} cues; {unresolved} unresolved term candidates",
    )
    return normalized_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--kind", choices=("subtitle", "asr"), default="asr")
    args = parser.parse_args()

    job_dir = args.job.expanduser().resolve()
    input_path = args.input.expanduser().resolve()
    try:
        normalized_path = normalize_transcript_file(job_dir, input_path, args.kind)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    print(normalized_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
