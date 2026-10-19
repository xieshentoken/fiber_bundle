"""Run an explicitly enabled finite local query batch; never start a background service."""

import argparse
import json
import signal
import sqlite3
from pathlib import Path
from typing import Any

from . import auth
from .config import StorageError, load_config
from .local_query_execution import stop
from .local_query_worker import dispatch
from .query_routing_config import load


def _interrupt(signum: int, frame: Any) -> None:
    raise KeyboardInterrupt()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config.test.json"))
    parser.add_argument("--routing-config", type=Path)
    parser.add_argument("--client-id")
    parser.add_argument("--max-tasks", type=int, default=0)
    parser.add_argument("--allow-inference", action="store_true")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--cancel-task")
    action.add_argument("--recover-task")
    args = parser.parse_args()
    previous = signal.signal(signal.SIGTERM, _interrupt)
    try:
        if not 0 <= args.max_tasks <= 100:
            raise ValueError()
        if (
            not args.cancel_task
            and not args.recover_task
            and (not args.allow_inference or args.max_tasks == 0)
        ):
            print(json.dumps({"state": "disabled", "processed": 0, "results": []}))
            return 0
        if not args.client_id or (
            not args.cancel_task and not args.recover_task and not args.routing_config
        ):
            raise ValueError()
        root = args.project_root.resolve(strict=True)
        config = load_config(args.config if args.config.is_absolute() else root / args.config, root)
        if args.cancel_task or args.recover_task:
            result = stop(
                config,
                args.client_id,
                args.cancel_task or args.recover_task,
                recover=args.recover_task is not None,
            )
        else:
            route_path = (
                args.routing_config
                if args.routing_config.is_absolute()
                else root / args.routing_config
            )
            result = dispatch(
                config,
                load(route_path),
                args.client_id,
                max_tasks=args.max_tasks,
                allow_inference=True,
            )
        print(json.dumps(result))
        return int(result["state"] in ("blocked", "stopped", "failed"))
    except KeyboardInterrupt:
        print(json.dumps({"state": "interrupted", "error_code": "local_worker_interrupted"}))
        return 130
    except (auth.Denied, StorageError, OSError, sqlite3.Error, ValueError):
        print(json.dumps({"state": "error", "error_code": "local_worker_rejected"}))
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    raise SystemExit(main())
