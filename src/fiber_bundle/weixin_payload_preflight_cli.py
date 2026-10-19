"""Inspect one explicitly selected offline JSON sample; emit redacted diagnostics only."""

import argparse
import json
import os
import stat
from pathlib import Path

from .config import InvalidInput
from .weixin_events import MAX_BYTES
from .weixin_payload_preflight import inspect


def read_sample(path: Path) -> bytes:
    path = path.absolute()
    if path.resolve() != path:
        raise InvalidInput("payload_file_path")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_BYTES:
            raise InvalidInput("payload_file_type_or_size")
        source = stream.read(MAX_BYTES + 1)
        after = os.fstat(stream.fileno())
        if len(source) != before.st_size or (before.st_size, before.st_mtime_ns) != (
            after.st_size,
            after.st_mtime_ns,
        ):
            raise InvalidInput("payload_file_changed")
    return source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--input-kind", choices=("ilink-poll", "normalized-chat"), required=True)
    args = parser.parse_args()
    try:
        result = inspect(read_sample(args.payload), args.input_kind)
    except (OSError, InvalidInput):
        # Do not echo private paths, arbitrary JSON keys, identities, text or exception data.
        print(json.dumps({"state": "rejected", "reason": "payload_preflight_failed"}))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
