"""Explicit local graph controls; run owns exactly one previously authorized admission."""

import argparse
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .. import auth, database
from ..config import StorageError, load_config
from . import catalog, jobs, policy, retrieval


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("configure", "run", "cancel", "quarantine", "status", "browse", "search")
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config.test.json"))
    parser.add_argument("--client-id")
    parser.add_argument("--vault-id")
    parser.add_argument("--job-id")
    parser.add_argument("--profile-file", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--query")
    parser.add_argument("--graph", action="store_true")
    args = parser.parse_args()
    result: Any
    try:
        root = args.project_root.resolve(strict=True)
        cfg = load_config(args.config if args.config.is_absolute() else root / args.config, root)
        if args.action == "configure":
            if args.profile_file is None:
                raise StorageError("profile file required")
            from ..query_routing_files import read
            from .contracts import read_json

            profile = policy.parse(read_json(read(args.profile_file, 16384), 16384))
            with database.locked(cfg):
                policy.save(cfg, profile, args.expected_sha256)
            result = {"configuration": profile.digest, "value": asdict(profile)}
        else:
            cid = args.client_id
            if args.action in {"browse", "search"}:
                if args.token_file is None:
                    raise StorageError("client token file required")
                from ..query_routing_files import read

                with database.locked(cfg):
                    db = database.connect(cfg.state_root)
                    try:
                        cid = auth.client(
                            db, "Bearer " + read(args.token_file, 256).decode().strip()
                        )["id"]
                    finally:
                        db.close()
            if cid is None or args.vault_id is None:
                raise StorageError("scope required")
            if args.action == "search":
                result = retrieval.recall(
                    cfg, cid, args.vault_id, args.query or "", enabled=args.graph
                )
            elif args.action == "browse":
                result = catalog.browse(cfg, cid, args.vault_id)
            elif args.action == "status":
                result = jobs.status(cfg, cid, args.vault_id, args.job_id)
            else:
                if args.job_id is None:
                    raise StorageError("job required")
                result = getattr(jobs, "execute" if args.action == "run" else args.action)(
                    cfg, cid, args.vault_id, args.job_id
                )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (StorageError, auth.Denied, OSError, ValueError, sqlite3.Error):
        print(json.dumps({"state": "error", "error_code": "graph_operation_rejected"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
