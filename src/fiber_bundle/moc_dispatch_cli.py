"""Schedule or inspect explicitly associated publications; dispatch is default-off."""

import argparse
import json
import sqlite3
from pathlib import Path

from . import auth, moc_dispatch
from .config import StorageError, load_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("schedule", "dispatch", "status", "reconcile"))
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config.test.json"))
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--vault-id", required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--request-id")
    parser.add_argument("--moc-id")
    parser.add_argument("--revision", type=int)
    parser.add_argument("--moc-path")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--job-id")
    parser.add_argument("--enabled", action="store_true")
    parser.add_argument("--maximum", type=int, default=0)
    args = parser.parse_args()
    try:
        root = args.project_root.resolve(strict=True)
        config = load_config(args.config if args.config.is_absolute() else root / args.config, root)
        with args.token_file.open("rb") as stream:
            token = stream.read(257)
        if len(token) > 256:
            raise StorageError("client token too large")
        authorization = "Bearer " + token.decode().strip()
        scope = (config, authorization, args.vault_id)
        inputs = (args.run_id, args.moc_id, args.revision, args.moc_path, args.expected_sha256)
        if args.action != "dispatch" and (args.enabled or args.maximum):
            raise StorageError("only dispatch accepts enabled/maximum")
        if args.action == "schedule":
            if args.job_id is not None or any(v is None for v in inputs):
                raise StorageError("schedule requires an explicit publication run and MOC plan")
            result = moc_dispatch.schedule(
                *scope,
                args.run_id,
                args.moc_id,
                revision=args.revision,
                relative=args.moc_path,
                expected_sha256=args.expected_sha256,
                request_id=args.request_id,
            )
        else:
            if any(v is not None for v in inputs) or args.request_id is not None:
                raise StorageError("only schedule accepts publication and MOC plan inputs")
            if args.action == "dispatch":
                if args.job_id is not None:
                    raise StorageError("dispatch does not accept job ID")
                result = moc_dispatch.dispatch(*scope, enabled=args.enabled, maximum=args.maximum)
            else:
                if args.job_id is None:
                    raise StorageError("status/reconcile require a job ID")
                operation = (
                    moc_dispatch.status if args.action == "status" else moc_dispatch.reconcile
                )
                result = operation(*scope, args.job_id)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (StorageError, auth.Denied, OSError, ValueError, sqlite3.Error):
        print(json.dumps({"state": "error", "error_code": "moc_dispatch_rejected"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
