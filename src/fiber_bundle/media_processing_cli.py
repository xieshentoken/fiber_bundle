"""Owner-authenticated local processing commands; no review impersonation or automatic retry."""

import argparse
import json
import sqlite3
from pathlib import Path

from . import auth, media_jobs, media_processing
from .client_token import read as token
from .config import StorageError, load_config
from .media_glossary_cli import read as read_file


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["create", "run", "status", "result", "cancel", "recover"]
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config.test.json"))
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--policy-file", type=Path)
    a = parser.parse_args()
    try:
        root = a.project_root.resolve(strict=True)
        config = load_config(a.config if a.config.is_absolute() else root / a.config, root)
        with media_jobs.connection(config) as db:
            cid = auth.client(db, "Bearer " + token(a.token_file))["id"]
        if a.command == "create":
            if a.policy_file is None:
                parser.error("create requires --policy-file")
            value = media_processing.create(
                config, cid, a.job_id, json.loads(read_file(a.policy_file))
            )
        elif a.command == "run":
            value = media_processing.run(config, cid, a.job_id)
        elif a.command == "result":
            value = media_processing.result(config, cid, a.job_id)
        elif a.command in ("cancel", "recover"):
            value = media_processing.stop(config, cid, a.job_id, recover=a.command == "recover")
        else:
            value = media_processing.status(config, cid, a.job_id)
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    except (StorageError, auth.Denied, OSError, ValueError, sqlite3.Error):
        print(json.dumps({"error": "media_processing_request_rejected"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
