"""Authenticated local operator interface for offline direct-file candidates."""

import argparse
import json
import sqlite3
import subprocess
from pathlib import Path

from . import auth, database, media_jobs
from .client_token import read as token
from .config import StorageError, load_config
from .media_worker import MediaPolicy


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("create", "run", "status", "result", "cancel", "recover")
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config.test.json"))
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--job-id")
    parser.add_argument("--receipt-id")
    parser.add_argument("--asset-id")
    parser.add_argument("--subtitle-receipt-id")
    parser.add_argument("--subtitle-asset-id")
    parser.add_argument("--mode", choices=("audio_only", "full_evidence"))
    parser.add_argument("--declared-duration-ms", type=int)
    parser.add_argument("--image")
    parser.add_argument("--timeout", type=int, default=90)
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
        if args.command == "create":
            if not all((args.receipt_id, args.asset_id, args.mode, args.image)) or (
                args.declared_duration_ms is None
            ):
                parser.error(
                    "create requires receipt, asset, explicit mode, duration and fixed image"
                )
            identifier = media_jobs.create(
                config,
                client_id,
                args.receipt_id,
                args.asset_id,
                args.mode,
                args.declared_duration_ms,
                MediaPolicy(args.image, args.timeout),
                subtitle_receipt_id=args.subtitle_receipt_id,
                subtitle_asset_id=args.subtitle_asset_id,
            )
            value = media_jobs.status(config, client_id, identifier)
        else:
            if not args.job_id:
                parser.error("--job-id required")
            if args.command == "run":
                value = media_jobs.run(config, client_id, args.job_id)
            elif args.command == "result":
                value = media_jobs.result(config, client_id, args.job_id)
            elif args.command == "status":
                value = media_jobs.status(config, client_id, args.job_id)
            else:
                value = media_jobs.stop(
                    config, client_id, args.job_id, recover=args.command == "recover"
                )
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    except (
        StorageError,
        auth.Denied,
        OSError,
        ValueError,
        sqlite3.Error,
        subprocess.SubprocessError,
    ):
        print(json.dumps({"error": "media_request_rejected"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
