"""Read-only M9 media capability and metadata preflight; no task side effects."""

import argparse
import json
import os
import stat
from pathlib import Path

from . import media_capabilities, media_preflight
from .config import InvalidInput
from .media_contract import MAX_MANIFEST_BYTES


def read_manifest(path: Path) -> bytes:
    path = path.absolute()
    if path.resolve() != path:
        raise InvalidInput("media_manifest_path")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_MANIFEST_BYTES:
            raise InvalidInput("media_manifest_type_or_size")
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
        after = os.fstat(stream.fileno())
        if len(raw) != before.st_size or (before.st_size, before.st_mtime_ns) != (
            after.st_size,
            after.st_mtime_ns,
        ):
            raise InvalidInput("media_manifest_changed")
    return raw


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    caps = commands.add_parser("capabilities")
    caps.add_argument("--skill-root", type=Path)
    probe = commands.add_parser("inspect")
    probe.add_argument("--manifest", type=Path, required=True)
    probe.add_argument("--permission", choices=("unverified", "denied"), default="unverified")
    probe.add_argument("--cancelled", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "capabilities":
            result = media_capabilities.inspect(args.skill_root)
        else:
            result = media_preflight.inspect(
                read_manifest(args.manifest), permission=args.permission, cancelled=args.cancelled
            )
    except OSError:
        print(json.dumps({"state": "rejected", "reason": "media_file_unavailable"}))
        return 1
    except InvalidInput:
        print(json.dumps({"state": "rejected", "reason": "media_manifest_invalid"}))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
