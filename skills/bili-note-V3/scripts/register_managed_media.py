#!/usr/bin/env python3
"""Register a job-owned media copy for safe post-publication cleanup."""

from __future__ import annotations

import argparse
from pathlib import Path

from common import register_managed_file


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument(
        "--kind",
        choices=("source_video", "source_audio", "derived_audio"),
        required=True,
    )
    parser.add_argument("--origin", required=True)
    args = parser.parse_args()

    job_dir = args.job.expanduser().resolve()
    path = args.path.expanduser()
    if not path.is_absolute():
        path = job_dir / path
    try:
        register_managed_file(
            job_dir,
            path,
            kind=args.kind,
            origin=args.origin,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
