"""Owner-authenticated glossary review and confirmation; no automatic user approval."""

import argparse
import json
import os
import sqlite3
import stat
from pathlib import Path

from . import auth, database, media_asr_preflight, media_glossary
from .client_token import read as token
from .config import InvalidInput, StorageError, load_config
from .media_glossary_contract import MAX_BYTES


def read(path: Path) -> bytes:
    if path.absolute() != path.absolute().resolve():
        raise InvalidInput("media_glossary_file_scope")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 0 < before.st_size <= MAX_BYTES
        ):
            raise InvalidInput("media_glossary_file_type_or_size")
        data = stream.read(MAX_BYTES + 1)
        after = os.fstat(stream.fileno())
        if len(data) != before.st_size or (before.st_size, before.st_mtime_ns) != (
            after.st_size,
            after.st_mtime_ns,
        ):
            raise InvalidInput("media_glossary_file_changed")
        return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "show", "status", "confirm", "preflight"))
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config.test.json"))
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--glossary-file", type=Path)
    parser.add_argument("--revision", type=int)
    parser.add_argument("--sha256")
    parser.add_argument("--allow-empty", action="store_true")
    args = parser.parse_args()
    try:
        root = args.project_root.resolve(strict=True)
        config = load_config(args.config if args.config.is_absolute() else root / args.config, root)
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                client_id = auth.client(db, "Bearer " + token(args.token_file))["id"]
            finally:
                db.close()
        if args.command == "freeze":
            if args.glossary_file is None:
                parser.error("freeze requires --glossary-file")
            value = media_glossary.freeze(config, client_id, args.job_id, read(args.glossary_file))
        elif args.command == "confirm":
            if args.revision is None or args.sha256 is None:
                parser.error("confirm requires reviewed --revision and --sha256")
            value = media_glossary.confirm(
                config,
                client_id,
                args.job_id,
                args.revision,
                args.sha256,
                allow_empty=args.allow_empty,
            )
        elif args.command == "show":
            value = media_glossary.show(config, client_id, args.job_id)
        elif args.command == "status":
            value = media_glossary.status(config, client_id, args.job_id)
        else:
            value = media_asr_preflight.inspect(config, client_id, args.job_id)
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    except (StorageError, auth.Denied, OSError, ValueError, sqlite3.Error):
        print(json.dumps({"error": "media_glossary_request_rejected"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
