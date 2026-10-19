"""Local worker startup, status and explicit publication/reconciliation."""

import argparse
import json
import signal
from pathlib import Path
from types import FrameType
from typing import NoReturn

from . import execution
from . import weixin_batch_revocation as revocation
from . import weixin_diagnostics as diagnostics
from . import weixin_publication as publication
from . import weixin_publication_dispatch as publishing
from . import weixin_task_dispatch as dispatch
from . import weixin_task_status as status
from . import weixin_worker as worker
from .config import contained, load_config
from .execution_config import load_execution_config
from .weixin_worker_config import load_worker_config, read_json


def _interrupt(signum: int, frame: FrameType | None) -> NoReturn:
    raise KeyboardInterrupt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "run",
            "status",
            "reconcile",
            "publication-plan",
            "publication-reconcile",
            "publish",
            "inspect",
            "revocation-plan",
            "revocation-apply",
            "revocation-reconcile",
            "revocation-status",
            "revocation-project-mocs",
            "media-terminology",
        ),
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config.test.json"))
    parser.add_argument(
        "--worker-config", type=Path, default=Path("configs/weixin-worker.example.json")
    )
    parser.add_argument("--run-id")
    parser.add_argument("--batch-id")
    parser.add_argument("--job-id")
    parser.add_argument("--after", type=int)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.command in {"status", "reconcile", "publication-reconcile"} and not args.run_id:
        parser.error("status/reconcile requires --run-id")
    if args.command not in {"status", "reconcile", "publication-reconcile"} and args.run_id:
        parser.error("this command does not accept --run-id")
    batch_commands = {
        "publication-plan",
        "revocation-plan",
        "revocation-apply",
        "revocation-reconcile",
        "revocation-status",
        "revocation-project-mocs",
    }
    if args.command in batch_commands and not args.batch_id:
        parser.error("publication-plan requires --batch-id")
    if args.batch_id and args.command not in batch_commands | {"inspect"}:
        parser.error("this command does not accept --batch-id")
    if args.command != "inspect" and (args.after is not None or args.limit is not None):
        parser.error("only inspect accepts --after/--limit")
    if (args.command == "media-terminology") != bool(args.job_id):
        parser.error("only media-terminology requires --job-id")
    old_handlers = {}
    try:
        root = args.project_root.resolve(strict=True)
        config = load_config(args.config if args.config.is_absolute() else root / args.config, root)
        settings = load_worker_config(config, args.worker_config)
        if args.command == "media-terminology":
            from .weixin_media_terminology import prepare

            result = prepare(config, settings.binding, execution.identifier(args.job_id))
        elif args.command == "inspect":
            result = diagnostics.inspect(
                config,
                settings.binding,
                batch_id=args.batch_id,
                after=args.after if args.after is not None else 0,
                limit=args.limit if args.limit is not None else 20,
            )
        elif args.command.startswith("revocation-"):
            result = revocation.run(
                config,
                settings.binding,
                execution.identifier(args.batch_id),
                args.command.removeprefix("revocation-"),
            )
        elif args.command == "run":
            policy = None
            if settings.enabled and settings.maximum:
                path = contained(root, settings.execution_config)
                read_json(config, path)  # Apply the same file bounds before the existing loader.
                policy = load_execution_config(path)
            for signum in (signal.SIGINT, signal.SIGTERM):
                old_handlers[signum] = signal.signal(signum, _interrupt)
            result = worker.run(config, settings, policy)
        elif args.command == "publication-plan":
            result = publication.plan(config, settings.binding, execution.identifier(args.batch_id))
        elif args.command == "publish":
            result = publishing.dispatch(
                config,
                settings.binding,
                maximum=settings.publication_maximum if settings.enabled else 0,
            )
        elif args.command == "publication-reconcile":
            result = publication.reconcile(
                config, settings.binding, execution.identifier(args.run_id)
            )
        else:
            run_id = execution.identifier(args.run_id)
            result = (
                status.synchronize(config, settings.binding, run_id)
                if args.command == "status"
                else dispatch.reconcile(config, settings.binding, run_id)
            )
        print(json.dumps(result))
        return int(result.get("state") in {"blocked", "stopped", "interrupted"})
    except KeyboardInterrupt:
        print(json.dumps({"state": "interrupted", "error_code": "weixin_worker_interrupted"}))
        return 130
    except Exception:
        # Provider/storage errors may contain credentials or raw input. Preserve durable state,
        # report a category, and never retry or erase an unresolved claim here.
        print(json.dumps({"state": "error", "error_code": "weixin_worker_rejected"}))
        return 1
    finally:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
