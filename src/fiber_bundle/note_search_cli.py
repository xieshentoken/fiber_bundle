"""Explicit local index build/status with a current client token; no model calls."""

import argparse
import json
import sqlite3
from pathlib import Path

from . import auth, database, note_search_build, note_search_cache
from .config import StorageError, load_config
from .query_routing_files import read


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "status"))
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config.test.json"))
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--vault-id", required=True)
    args = parser.parse_args()
    try:
        root = args.project_root.resolve(strict=True)
        cfg = load_config(args.config if args.config.is_absolute() else root / args.config, root)
        with database.locked(cfg):
            db = database.connect(cfg.state_root)
            try:
                cid = auth.client(db, "Bearer " + read(args.token_file, 256).decode().strip())["id"]
                vault = auth.grant(db, cid, args.vault_id, "query")
                result = (
                    note_search_cache.status(cfg, db, vault) if args.action == "status" else None
                )
            finally:
                db.close()
        if args.action == "build":
            assert cid is not None
            result = note_search_build.build(cfg, cid, args.vault_id)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (StorageError, auth.Denied, OSError, ValueError, sqlite3.Error):
        print(json.dumps({"state": "error", "error_code": "note_index_operation_rejected"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
