"""Offline draft review: prepare a packet, then record an explicit assessment. No model calls."""

import argparse
import json
import os
from pathlib import Path

from . import draft_review
from .answer_model_output import reject_constant, unique_object
from .config import InvalidInput
from .draft_review_summary import summarize

MAX_BYTES = 1024 * 1024


def read_json(path: Path) -> object:
    with path.open("rb") as source:
        raw = source.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise InvalidInput("review input too large")
    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)


def write_new(path: Path, value: object) -> None:
    data = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as target:
        target.write(data)
        target.flush()
        os.fsync(target.fileno())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("prepare", "record"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--fixture", type=Path, required=True)
        cmd.add_argument("--proposal", type=Path, required=True)
        cmd.add_argument("--output", type=Path, required=True)
        if name == "prepare":
            cmd.add_argument("--case-id", required=True)
        else:
            cmd.add_argument("--packet", type=Path, required=True)
            cmd.add_argument("--assessment", type=Path, required=True)
    summary = sub.add_parser("summarize")
    summary.add_argument("--fixture", type=Path, required=True)
    summary.add_argument("--batch", type=Path, required=True)
    summary.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        fixture = read_json(args.fixture)
        if args.action == "summarize":
            result = summarize(fixture, read_json(args.batch))
            write_new(args.output, result)
            print(
                json.dumps(
                    {"review_coverage": result["review_coverage"], "counts": result["counts"]}
                )
            )
            return 0
        candidate = read_json(args.proposal)
        if args.action == "prepare":
            result = draft_review.prepare(fixture, args.case_id, candidate)
        else:
            result = draft_review.record(
                fixture, candidate, read_json(args.packet), read_json(args.assessment)
            )
        write_new(args.output, result)
        print(json.dumps({"review_state": result["review_state"], "case_id": result["case_id"]}))
        return 0
    except (InvalidInput, ValueError, TypeError, RecursionError, OSError):
        print(json.dumps({"error": "draft_review_rejected"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
