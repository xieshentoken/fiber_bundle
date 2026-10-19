"""Run a bounded batch of queued answer tasks independently of the HTTP service."""

import argparse
import json
import sqlite3
from pathlib import Path

from .answer_worker import dispatch
from .config import StorageError, load_config
from .execution_config import load_execution_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config.test.json"))
    parser.add_argument("--execution-config", type=Path, required=True)
    parser.add_argument("--max-tasks", type=int, default=1)
    args = parser.parse_args()
    try:
        root = args.project_root.resolve(strict=True)
        config = load_config(args.config if args.config.is_absolute() else root / args.config, root)
        result = dispatch(
            config, load_execution_config(args.execution_config), max_tasks=args.max_tasks
        )
        print(json.dumps(result))
        return int(result["state"] in ("blocked", "stopped"))
    except KeyboardInterrupt:
        print(json.dumps({"state": "interrupted", "error_code": "worker_interrupted"}))
        return 130
    except (StorageError, OSError, sqlite3.Error, ValueError):
        print(json.dumps({"state": "error", "error_code": "answer_worker_rejected"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
