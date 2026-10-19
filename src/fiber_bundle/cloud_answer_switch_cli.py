"""Explicit host approval of one declared backup, with no inference."""

import argparse
import json
import sqlite3
from pathlib import Path

from . import auth, cloud_answer_selection, database
from .config import StorageError, load_config
from .query_routing_config import load


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config.test.json"))
    parser.add_argument("--routing-config", type=Path)
    parser.add_argument("--client-id")
    parser.add_argument("--run-id")
    parser.add_argument("--backup-index", type=int)
    parser.add_argument("--selection-id")
    parser.add_argument("--approve-backup", action="store_true")
    args = parser.parse_args()
    try:
        if not args.approve_backup:
            print(json.dumps({"state": "disabled", "model_calls": 0}))
            return 0
        if not all((args.routing_config, args.client_id, args.run_id, args.selection_id)):
            raise ValueError()
        if args.backup_index is None or not 1 <= args.backup_index <= 4:
            raise ValueError()
        root = args.project_root.resolve(strict=True)
        config = load_config(args.config if args.config.is_absolute() else root / args.config, root)
        route = load(
            args.routing_config if args.routing_config.is_absolute() else root / args.routing_config
        )
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                result = cloud_answer_selection.select(
                    config,
                    db,
                    route,
                    args.client_id,
                    args.run_id,
                    args.backup_index,
                    args.selection_id,
                    allow_switch=True,
                )
            finally:
                db.close()
        print(json.dumps(result))
        return int(result["state"] != "selected")
    except (auth.Denied, StorageError, OSError, sqlite3.Error, ValueError):
        print(json.dumps({"state": "error", "error_code": "cloud_selection_rejected"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
