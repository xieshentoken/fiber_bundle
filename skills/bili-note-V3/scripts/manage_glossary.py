#!/usr/bin/env python3
"""Add, inspect, and explicitly confirm domain terminology for a job."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from common import read_json, sha256_json, update_pipeline_state, utc_now, write_json


def glossary_path(job_dir: Path) -> Path:
    return job_dir / "terms" / "glossary.json"


def render_review(job_dir: Path, glossary: dict[str, Any]) -> None:
    lines = ["# Terminology review", "", f"Topic: {glossary.get('topic') or '(not set)'}", ""]
    terms = glossary.get("terms", [])
    if not terms:
        lines.append("No terms have been added.")
    for term in terms:
        lines.extend(
            [
                f"## {term['canonical']}",
                "",
                f"- Full name: {term.get('full_name') or '(not set)'}",
                f"- Chinese name: {term.get('chinese_name') or '(not set)'}",
                f"- Type: {term.get('type') or 'term'}",
                f"- Context: {', '.join(term.get('context_keywords', [])) or '(not set)'}",
                "- Safe aliases: "
                + (", ".join(alias["value"] for alias in term.get("aliases", []) if alias["mode"] == "safe") or "(none)"),
                "- Contextual aliases: "
                + (", ".join(alias["value"] for alias in term.get("aliases", []) if alias["mode"] == "contextual") or "(none)"),
                "",
            ]
        )
    lines.extend(
        [
            "## Confirmation",
            "",
            f"Confirmed: {bool(glossary.get('confirmed'))}",
            f"Confirmed by: {glossary.get('confirmed_by') or '(not confirmed)'}",
            "",
            "Safe aliases may be replaced directly. Contextual aliases are changed only when nearby transcript text contains a configured context keyword.",
        ]
    )
    (job_dir / "terms" / "term_review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_asr_context(job_dir: Path, glossary: dict[str, Any]) -> None:
    hotwords: list[str] = []
    descriptions: list[str] = []
    for term in glossary.get("terms", []):
        values = [
            term.get("canonical", ""),
            term.get("full_name", ""),
            term.get("chinese_name", ""),
        ]
        hotwords.extend(value for value in values if value)
        if term.get("full_name"):
            descriptions.append(f"{term['canonical']} means {term['full_name']}")
    context = {
        "confirmed": bool(glossary.get("confirmed")),
        "topic": glossary.get("topic", ""),
        "hotwords": list(dict.fromkeys(hotwords)),
        "initial_prompt": "; ".join(descriptions),
        "glossary_hash": glossary.get("confirmed_hash"),
    }
    write_json(job_dir / "terms" / "asr_context.json", context)


def term_payload(args: argparse.Namespace) -> dict[str, Any]:
    aliases = [{"value": value, "mode": "safe"} for value in args.safe_alias]
    aliases.extend({"value": value, "mode": "contextual"} for value in args.alias)
    return {
        "canonical": args.canonical,
        "full_name": args.full_name,
        "chinese_name": args.chinese_name,
        "type": args.type,
        "preserve_case": args.preserve_case,
        "context_keywords": list(dict.fromkeys(args.context)),
        "aliases": aliases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser("add", help="Add or replace one canonical term")
    add.add_argument("job", type=Path)
    add.add_argument("--canonical", required=True)
    add.add_argument("--full-name", default="")
    add.add_argument("--chinese-name", default="")
    add.add_argument("--type", default="term")
    add.add_argument("--context", action="append", default=[])
    add.add_argument("--alias", action="append", default=[], help="Ambiguous/contextual alias")
    add.add_argument("--safe-alias", action="append", default=[])
    add.add_argument("--preserve-case", action=argparse.BooleanOptionalAction, default=True)

    confirm = subparsers.add_parser("confirm", help="Confirm the current glossary")
    confirm.add_argument("job", type=Path)
    confirm.add_argument("--by", required=True)
    confirm.add_argument("--allow-empty", action="store_true")

    show = subparsers.add_parser("show", help="Render the current glossary review")
    show.add_argument("job", type=Path)

    args = parser.parse_args()
    job_dir = args.job.expanduser().resolve()
    path = glossary_path(job_dir)
    glossary = read_json(path)

    if args.command == "add":
        value = term_payload(args)
        terms = glossary.setdefault("terms", [])
        terms[:] = [
            term
            for term in terms
            if term.get("canonical", "").casefold() != args.canonical.casefold()
        ]
        terms.append(value)
        terms.sort(key=lambda term: term["canonical"].casefold())
        glossary.update(
            {
                "confirmed": False,
                "confirmed_at": None,
                "confirmed_by": None,
                "confirmed_hash": None,
            }
        )
        update_pipeline_state(job_dir, "terminology", "blocked", "Glossary changed; reconfirm it")
    elif args.command == "confirm":
        if not glossary.get("terms") and not args.allow_empty:
            parser.error("The glossary is empty; use --allow-empty only after explicit user review")
        confirmed_at = utc_now()
        glossary["confirmed"] = True
        glossary["confirmed_at"] = confirmed_at
        glossary["confirmed_by"] = args.by
        glossary["confirmed_hash"] = sha256_json(glossary.get("terms", []))
        update_pipeline_state(job_dir, "terminology", "complete", f"Confirmed by {args.by}")

    write_json(path, glossary)
    render_review(job_dir, glossary)
    write_asr_context(job_dir, glossary)
    print(job_dir / "terms" / "term_review.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
